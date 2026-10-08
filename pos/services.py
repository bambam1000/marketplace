"""Règles métier de la caisse (POS).

Les vues ne font qu'appeler ces fonctions : toute vérification (droits, stock de l'entrepôt de la caisse,
plafond de remise, espèces disponibles, état de la session) est faite ici, dans une transaction.
Une erreur métier lève PosError avec un message prêt à afficher.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F, Q, Sum, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.utils import timezone

from store import access
from .models import CashMovement, CashRegister, POSRefund, POSRefundItem, POSSale, POSSaleItem, POSSession

MONEY = DecimalField(max_digits=18, decimal_places=2)
# Chiffre d'affaires hors TVA d'une vente, remboursements déduits (pour les agrégations)
NET_REVENUE = ExpressionWrapper(F('total_amount') - F('tax_amount') - F('refunded_amount') + F('refunded_tax'),
                                output_field=MONEY)


def item_cost(prefix=''):
    """Coût des articles vendus d'une ligne POS, quantités remboursées déduites."""
    unit = Coalesce(f'{prefix}unit_cost', f'{prefix}product__cost_price', output_field=MONEY)
    return ExpressionWrapper((F(f'{prefix}quantity') - F(f'{prefix}refunded_quantity')) * unit, output_field=MONEY)


class PosError(Exception):
    pass


def to_decimal(value, default=Decimal('0')):
    if value in (None, ''):
        return default
    try:
        return Decimal(str(value).replace(' ', '').replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise PosError('Montant invalide.')


def to_quantity(value):
    qty = to_decimal(value, Decimal('1'))
    if qty != qty.to_integral_value():
        raise PosError('La quantité doit être un nombre entier.')
    return int(qty)


# ───────────────────────── Droits ─────────────────────────

@dataclass
class Scope:
    store: object
    can_use: bool       # encaisser sur une caisse
    can_manage: bool    # gérer les caisses, rembourser, remises au-delà du plafond, fermer la caisse d'un autre
    is_owner: bool
    limit: int | None   # entrepôt auquel l'employé est limité

    def warehouse_ok(self, warehouse_id):
        return self.limit is None or self.limit == warehouse_id


def scope_for(user):
    store = access.acting_store(user)
    if store is None:
        return None
    owner = access.has_perm(user, store, None)
    manage = owner or access.has_perm(user, store, 'pos.manage')
    use = manage or access.has_perm(user, store, 'pos.use')
    return Scope(store, use, manage, owner, access.member_warehouse_id(user, store))


def registers_for(scope, include_inactive=False):
    qs = CashRegister.objects.filter(store=scope.store).select_related('warehouse')
    if not include_inactive:
        qs = qs.filter(is_active=True, warehouse__is_active=True)
    if scope.limit:
        qs = qs.filter(warehouse_id=scope.limit)
    return qs


def sessions_for(scope, user):
    """Sessions visibles : toutes (responsable) ou seulement les siennes (caissier)."""
    qs = POSSession.objects.filter(store=scope.store).select_related('register', 'warehouse', 'cashier')
    if scope.limit:
        qs = qs.filter(warehouse_id=scope.limit)
    if not scope.can_manage:
        qs = qs.filter(cashier=user)
    return qs


def sales_for(scope, user):
    qs = POSSale.objects.filter(store=scope.store)
    if scope.limit:
        qs = qs.filter(warehouse_id=scope.limit)
    if not scope.can_manage:
        qs = qs.filter(cashier=user)
    return qs


def ensure_store_warehouse(store):
    """Boutique sans entrepôt (anciens comptes) : crée l'entrepôt principal, comme à l'inscription,
    et y place le stock existant des produits (jusqu'ici non rattaché à un entrepôt)."""
    from inventory.models import ProductStock, Warehouse
    if store.warehouses.exists():
        return None
    warehouse = Warehouse.objects.create(store=store, name='Entrepôt principal', code=f'{store.id:03d}-MAIN',
                                         city=getattr(store, 'city', '') or 'Douala', is_default=True)
    ProductStock.objects.bulk_create([ProductStock(product=p, warehouse=warehouse, quantity=p.stock)
                                      for p in store.products.filter(stock__gt=0)])
    return warehouse


def ensure_default_register(warehouse):
    """Chaque entrepôt actif a au moins une caisse."""
    register = CashRegister.objects.filter(warehouse=warehouse).order_by('-is_active', 'pk').first()
    if register is None:
        register = CashRegister.objects.create(store=warehouse.store, warehouse=warehouse, name='Caisse principale')
    return register


def current_session(user, store):
    return (POSSession.objects.filter(store=store, cashier=user, status='open')
            .select_related('register', 'warehouse').first())


# ───────────────────────── Sessions ─────────────────────────

def open_session(user, scope, register, opening_cash, notes=''):
    if not scope.can_use:
        raise PosError("Vous n'avez pas le droit d'utiliser la caisse.")
    if register.store_id != scope.store.pk or not scope.warehouse_ok(register.warehouse_id):
        raise PosError("Cette caisse n'est pas dans votre entrepôt.")
    if not register.is_active or not register.warehouse.is_active:
        raise PosError('Cette caisse est désactivée.')
    opening_cash = to_decimal(opening_cash)
    if opening_cash < 0:
        raise PosError('Fond de caisse invalide.')
    with transaction.atomic():
        CashRegister.objects.select_for_update().get(pk=register.pk)
        busy = register.sessions.filter(status='open').select_related('cashier').first()
        if busy:
            raise PosError(f'{register.name} est déjà ouverte par {busy.cashier.display_name}.')
        mine = current_session(user, scope.store)
        if mine:
            raise PosError(f'Vous avez déjà une caisse ouverte ({mine.register.name if mine.register else mine.session_number}). Fermez-la d\'abord.')
        session = POSSession.objects.create(store=scope.store, register=register, warehouse=register.warehouse,
                                            cashier=user, opening_cash=opening_cash, notes=notes[:500])
        CashMovement.objects.create(session=session, type='in', category='opening', amount=opening_cash,
                                    description="Fond de caisse d'ouverture", created_by=user)
    return session


def close_session(session, user, scope, counted, cash_count=None, notes=''):
    if session.status != 'open':
        raise PosError('Cette session est déjà fermée.')
    if session.cashier_id != user.pk and not scope.can_manage:
        raise PosError('Seul le caissier ou un responsable peut fermer cette caisse.')
    counted = to_decimal(counted, None)
    if counted is None or counted < 0:
        raise PosError('Saisissez le montant compté dans le tiroir.')
    with transaction.atomic():
        session = POSSession.objects.select_for_update().get(pk=session.pk)
        expected = session.cash_balance()
        if counted != expected and not notes.strip():
            raise PosError(f'Écart de {int(counted - expected):+,} F : expliquez-le dans la note avant de fermer.'.replace(',', ' '))
        # Paniers non encaissés : annulés (le stock n'a pas bougé)
        session.sales.filter(status__in=['pending', 'held']).update(status='cancelled')
        if notes.strip():
            session.notes = (session.notes + '\n' if session.notes else '') + notes.strip()[:1000]
        session.close_session(counted, user=user, cash_count=cash_count or {})
    return session


def add_cash_movement(session, user, kind, category, amount, description):
    if session.status != 'open':
        raise PosError('La caisse est fermée.')
    if kind not in ('in', 'out'):
        raise PosError('Type de mouvement invalide.')
    allowed = CashMovement.MANUAL_IN if kind == 'in' else CashMovement.MANUAL_OUT
    if category not in allowed:
        raise PosError('Catégorie invalide.')
    amount = to_decimal(amount)
    if amount <= 0:
        raise PosError('Le montant doit être positif.')
    if not description.strip():
        raise PosError('Indiquez le motif du mouvement.')
    with transaction.atomic():
        session = POSSession.objects.select_for_update().get(pk=session.pk)
        if kind == 'out' and amount > session.cash_balance():
            raise PosError(f'Il n\'y a que {int(session.cash_balance()):,} F en espèces dans la caisse.'.replace(',', ' '))
        move = CashMovement.objects.create(session=session, type=kind, category=category, amount=amount,
                                           description=description.strip()[:500], created_by=user)
        session.calculate_totals()
    return move


# ───────────────────────── Panier ─────────────────────────

def warehouse_stock(product, warehouse):
    """Stock vendable à la caisse : celui de l'entrepôt (borné par le stock global)."""
    if warehouse is None:
        return product.stock
    from inventory.models import ProductStock
    here = ProductStock.objects.filter(product=product, warehouse=warehouse).values_list('quantity', flat=True).first() or 0
    return min(here, product.stock)


def open_sale(session, user):
    """Panier en cours de la session (créé si besoin)."""
    sale = session.sales.filter(status='pending').order_by('-pk').first()
    if sale is None:
        sale = POSSale.objects.create(session=session, store=session.store, cashier=user, warehouse=session.warehouse)
    return sale


def editable_sale(user, sale_id):
    """Panier modifiable par cet utilisateur : sa session ouverte, vente non encaissée."""
    sale = (POSSale.objects.select_related('session', 'session__register', 'store')
            .filter(pk=sale_id, status__in=['pending', 'held'], session__status='open', session__cashier=user).first())
    if sale is None:
        raise PosError('Vente introuvable ou déjà encaissée.')
    return sale


def add_item(sale, product, quantity=1):
    if product.store_id != sale.store_id or not product.is_active:
        raise PosError('Produit introuvable.')
    quantity = to_quantity(quantity)
    if quantity <= 0:
        raise PosError('Quantité invalide.')
    item = sale.items.filter(product=product).first()
    wanted = quantity + (int(item.quantity) if item else 0)
    available = warehouse_stock(product, sale.warehouse)
    if wanted > available:
        raise PosError(f'Stock insuffisant pour « {product.name} » : {available} disponible(s) ici.')
    if item:
        item.quantity = wanted
        item.save()
    else:
        item = POSSaleItem.objects.create(sale=sale, product=product, product_name=product.name,
                                          product_sku=product.sku or '', quantity=quantity, unit_price=product.price)
    return item


def set_quantity(item, quantity):
    quantity = to_quantity(quantity)
    if quantity <= 0:
        sale = item.sale
        item.delete()
        sale.calculate_totals()
        return None
    if item.product:
        available = warehouse_stock(item.product, item.sale.warehouse)
        if quantity > available:
            raise PosError(f'Stock insuffisant pour « {item.product_name} » : {available} disponible(s) ici.')
    item.quantity = quantity
    item.save()
    return item


def apply_discount(sale, scope, percent=None, amount=None):
    percent = to_decimal(percent)
    amount = to_decimal(amount)
    if percent < 0 or amount < 0 or percent > 100:
        raise PosError('Remise invalide.')
    if sale.subtotal <= 0 and (percent or amount):
        raise PosError('Ajoutez des articles avant la remise.')
    effective = percent if percent else (amount * 100 / sale.subtotal if sale.subtotal else Decimal('0'))
    ceiling = sale.session.register.max_discount_percent if sale.session.register else Decimal('100')
    if effective > ceiling and not scope.can_manage:
        shown = f'{ceiling:.0f}' if ceiling == ceiling.to_integral_value() else f'{ceiling.normalize():f}'.replace('.', ',')
        raise PosError(f'Remise maximale autorisée sur cette caisse : {shown} %. Demandez à un responsable.')
    if amount > sale.subtotal:
        raise PosError('La remise dépasse le montant des articles.')
    sale.discount_percent = percent
    sale.discount_amount = Decimal('0') if percent else amount
    sale.calculate_totals()
    return sale


def hold_sale(sale, label=''):
    if not sale.items.exists():
        raise PosError('Le panier est vide.')
    sale.status = 'held'
    sale.held_label = (label.strip() or sale.customer_name or f'Panier {timezone.localtime():%H:%M}')[:60]
    sale.save(update_fields=['status', 'held_label'])
    return sale


def resume_sale(session, user, sale_id):
    """Reprend un panier en attente ; le panier en cours (s'il n'est pas vide) passe en attente à sa place."""
    held = session.sales.filter(pk=sale_id, status='held').first()
    if held is None:
        raise PosError('Panier en attente introuvable.')
    with transaction.atomic():
        for current in session.sales.filter(status='pending'):
            if current.items.exists():
                hold_sale(current)
            else:
                current.delete()
        held.status = 'pending'
        held.save(update_fields=['status'])
    return held


# ───────────────────────── Encaissement ─────────────────────────

def checkout(sale, user, cash=0, card=0, mobile_money=0, customer_name='', customer_phone='', reference=''):
    sale.cash_amount = to_decimal(cash)
    sale.card_amount = to_decimal(card)
    sale.mobile_money_amount = to_decimal(mobile_money)
    if min(sale.cash_amount, sale.card_amount, sale.mobile_money_amount) < 0:
        raise PosError('Montant invalide.')
    sale.amount_tendered = sale.cash_amount
    sale.customer_name = customer_name.strip()[:200]
    sale.customer_phone = customer_phone.strip()[:20]
    sale.payment_reference = reference.strip()[:100]
    return finalize_sale(sale, user)


def finalize_sale(sale, user):
    """Encaisse la vente : contrôle du paiement et du stock de l'entrepôt, sortie de stock, journal de caisse."""
    from inventory.models import ProductStock
    with transaction.atomic():
        locked = POSSale.objects.select_for_update().get(pk=sale.pk)
        if locked.status not in ('pending', 'held'):
            raise PosError('Cette vente est déjà encaissée.')
        session = POSSession.objects.select_for_update().get(pk=sale.session_id)
        if session.status != 'open':
            raise PosError('La caisse est fermée.')
        items = list(sale.items.select_related('product'))
        if not items:
            raise PosError('Le panier est vide.')
        sale.calculate_totals()

        paid = sale.cash_amount + sale.card_amount + sale.mobile_money_amount
        if paid < sale.total_amount:
            raise PosError(f'Montant insuffisant : il manque {int(sale.total_amount - paid):,} F.'.replace(',', ' '))
        change = paid - sale.total_amount
        if change > sale.cash_amount:
            raise PosError('La carte et le Mobile Money ne peuvent pas dépasser le montant dû (seules les espèces donnent lieu à de la monnaie).')

        # Re-contrôle du stock sous verrou (deux caisses peuvent vendre le même article)
        for item in items:
            if not item.product:
                continue
            ps = ProductStock.objects.select_for_update().filter(product=item.product, warehouse=sale.warehouse).first()
            available = min(ps.quantity if ps else 0, item.product.stock) if sale.warehouse else item.product.stock
            if item.quantity > available:
                raise PosError(f'Stock insuffisant pour « {item.product_name} » : {available} disponible(s).')

        sale.change_amount = change
        sale.status = 'completed'
        sale.completed_at = timezone.now()
        sale.save()

        for item in items:
            if item.product:
                qty = int(item.quantity)
                type(item.product).objects.filter(pk=item.product_id).update(orders_count=F('orders_count') + qty)
                item.product.adjust_stock(-qty, 'pos', user=user, reason='Vente caisse',
                                          reference=sale.sale_number, warehouse=sale.warehouse)

        drawer = sale.cash_amount - change
        if drawer > 0:
            CashMovement.objects.create(session=session, type='in', category='sale', amount=drawer,
                                        description=f'Vente {sale.sale_number}', reference=sale.sale_number,
                                        created_by=user)
        session.calculate_totals()
    return sale


# ───────────────────────── Remboursement ─────────────────────────

def refund_sale(sale, user, session=None, lines=None, method=None, reason='', restock=True):
    """Rembourse tout ou partie d'une vente.

    lines : {id de ligne: quantité} ; None = tout ce qui reste remboursable.
    L'argent sort de `session` (la caisse ouverte de l'utilisateur), pas de la session d'origine,
    qui peut être fermée : ses chiffres ne bougent donc pas.
    """
    with transaction.atomic():
        sale = POSSale.objects.select_for_update().select_related('session').get(pk=sale.pk)
        if sale.status != 'completed':
            raise PosError('Seules les ventes encaissées peuvent être remboursées.')
        if session is None:
            session = current_session(user, sale.store) or sale.session
        session = POSSession.objects.select_for_update().get(pk=session.pk)
        if session.status != 'open':
            raise PosError('Ouvrez une caisse pour effectuer un remboursement.')
        if session.store_id != sale.store_id:
            raise PosError('Vente introuvable.')

        items = {i.pk: i for i in sale.items.select_related('product')}
        if lines is None:
            wanted = {pk: i.refundable_quantity for pk, i in items.items() if i.refundable_quantity > 0}
        else:
            wanted = {}
            for pk, qty in lines.items():
                qty = to_quantity(qty)
                item = items.get(int(pk))
                if item is None or qty < 0:
                    raise PosError('Ligne de vente invalide.')
                if qty > item.refundable_quantity:
                    raise PosError(f'« {item.product_name} » : {int(item.refundable_quantity)} au plus à rembourser.')
                if qty:
                    wanted[item.pk] = Decimal(qty)
        if not wanted:
            raise PosError('Choisissez au moins un article à rembourser.')

        # Montant : prix de la ligne × part de la remise globale et de la TVA de la vente
        factor = (sale.total_amount / sale.subtotal) if sale.subtotal else Decimal('1')
        everything = all(wanted.get(pk, 0) == i.refundable_quantity for pk, i in items.items())
        amounts = {pk: (items[pk].total / items[pk].quantity * qty * factor).quantize(Decimal('1'))
                   for pk, qty in wanted.items()}
        amount = sale.refundable_amount if everything else min(sum(amounts.values()), sale.refundable_amount)
        tax = (amount * sale.tax_amount / sale.total_amount).quantize(Decimal('1')) if sale.total_amount else Decimal('0')

        method = method or sale.main_payment_method
        if method not in ('cash', 'card', 'mobile_money'):
            raise PosError('Moyen de remboursement invalide.')
        if method == 'cash' and amount > session.cash_balance():
            raise PosError(f'Espèces insuffisantes dans la caisse ({int(session.cash_balance()):,} F) : remboursez par Mobile Money ou carte.'.replace(',', ' '))

        refund = POSRefund.objects.create(sale=sale, session=session, method=method, amount=amount, tax_amount=tax,
                                          reason=reason.strip()[:255], restock=restock, created_by=user)
        warehouse = session.warehouse or sale.warehouse
        for pk, qty in wanted.items():
            item = items[pk]
            POSRefundItem.objects.create(refund=refund, sale_item=item, quantity=qty, amount=amounts[pk])
            POSSaleItem.objects.filter(pk=pk).update(refunded_quantity=F('refunded_quantity') + qty)
            if restock and item.product:
                item.product.adjust_stock(int(qty), 'return', user=user, reason=f'Remboursement {refund.refund_number}',
                                          reference=sale.sale_number, warehouse=warehouse)

        sale.refunded_amount += amount
        sale.refunded_tax += tax
        if everything:
            sale.status = 'refunded'
        sale.save(update_fields=['refunded_amount', 'refunded_tax', 'status'])
        if method == 'cash':
            CashMovement.objects.create(session=session, type='out', category='refund', amount=amount,
                                        description=f'Remboursement {sale.sale_number}', reference=refund.refund_number,
                                        created_by=user)
        session.calculate_totals()
    return refund


# ───────────────────────── Statistiques ─────────────────────────

def figures(sales):
    """Chiffres d'un ensemble de ventes encaissées (remboursées comprises)."""
    sold = sales.filter(status__in=['completed', 'refunded'])
    agg = sold.aggregate(gross=Sum('total_amount'), refunded=Sum('refunded_amount'), tax=Sum('tax_amount'),
                         rtax=Sum('refunded_tax'), disc=Sum('discount_amount'), cash=Sum('cash_amount'),
                         change=Sum('change_amount'), card=Sum('card_amount'), momo=Sum('mobile_money_amount'))
    count = sold.count()
    gross = agg['gross'] or 0
    refunded = agg['refunded'] or 0
    cost = POSSaleItem.objects.filter(sale__in=sold).aggregate(c=Sum(item_cost()))['c'] or 0
    net_ht = gross - refunded - (agg['tax'] or 0) + (agg['rtax'] or 0)
    return {
        'count': count, 'gross': gross, 'refunded': refunded, 'net': gross - refunded, 'net_ht': net_ht,
        'tax': (agg['tax'] or 0) - (agg['rtax'] or 0), 'discounts': agg['disc'] or 0,
        'avg': (gross / count) if count else 0,
        'cash': (agg['cash'] or 0) - (agg['change'] or 0), 'card': agg['card'] or 0, 'momo': agg['momo'] or 0,
        'cost': cost, 'margin': net_ht - cost,
    }
