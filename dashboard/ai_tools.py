"""Outils de l'assistant IA : lecture seule, limités à la boutique et aux permissions de l'utilisateur.

Chaque outil : une définition envoyée à Claude (TOOLS) + une fonction Python (HANDLERS).
Les arguments viennent du modèle : ils sont validés ici avant toute requête en base.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from store import access

MAX_RESULT_CHARS = 20000
MAX_LIMIT = 50


class ToolInputError(ValueError):
    """Argument invalide : renvoyé au modèle comme erreur pour qu'il corrige son appel."""


class ToolContext:
    def __init__(self, user, store):
        self.user = user
        self.store = store

    def scope(self, permission):
        """(boutique, entrepôt limite) si l'utilisateur a la permission, sinon ToolInputError."""
        if self.store is None:
            raise ToolInputError("Aucune boutique associée à ce compte.")
        if not access.has_perm(self.user, self.store, permission):
            raise ToolInputError(f"Permission refusée : l'utilisateur n'a pas le droit « {permission or 'propriétaire'} ».")
        return self.store, access.member_warehouse_id(self.user, self.store)


# ───────────────────────── Validation des arguments ─────────────────────────
def _date(args, key, default):
    value = args.get(key)
    if value in (None, ''):
        return default
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        raise ToolInputError(f"{key} doit être une date AAAA-MM-JJ (reçu : {value!r}).")


def _period(args, default_days=30):
    today = timezone.localdate()
    end = _date(args, 'end_date', today)
    start = _date(args, 'start_date', end - timedelta(days=default_days - 1))
    if start > end:
        raise ToolInputError('start_date doit être antérieure ou égale à end_date.')
    if (end - start).days > 3660:
        raise ToolInputError('Période trop longue (10 ans maximum).')
    return start, end


def _int(args, key, default, minimum=1, maximum=MAX_LIMIT):
    value = args.get(key, default)
    if value in (None, ''):
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ToolInputError(f'{key} doit être un entier.')
    return max(minimum, min(maximum, value))


def _choice(args, key, choices, default):
    value = args.get(key) or default
    if value not in choices:
        raise ToolInputError(f"{key} doit valoir l'une de ces valeurs : {', '.join(choices)}.")
    return value


def _text(args, key, max_len=100):
    value = args.get(key)
    return str(value).strip()[:max_len] if value not in (None, '') else ''


def _money(value):
    return int(value or 0)


def _warehouse(ctx_store, limit, code):
    """Entrepôt de la boutique désigné par son code (ou celui de l'employé limité)."""
    from inventory.models import Warehouse
    warehouses = Warehouse.objects.filter(store=ctx_store)
    if limit:
        warehouses = warehouses.filter(pk=limit)
    if not code:
        return warehouses.filter(pk=limit).first() if limit else None
    wh = warehouses.filter(code__iexact=code).first()
    if wh is None:
        raise ToolInputError(f"Entrepôt « {code} » introuvable. Utilisez lister_entrepots pour voir les codes.")
    return wh


def _paid_items(store, start, end, warehouse=None):
    from orders.models import OrderItem
    items = (OrderItem.objects.filter(store=store, order__is_paid=True,
                                      order__created_at__date__gte=start, order__created_at__date__lte=end)
             .exclude(order__status__in=['cancelled', 'refunded']))
    return items.filter(warehouse=warehouse) if warehouse else items


def _pos_sales(store, start, end, warehouse=None):
    from pos.models import POSSale
    sales = POSSale.objects.filter(store=store, status='completed',
                                   created_at__date__gte=start, created_at__date__lte=end)
    return sales.filter(warehouse=warehouse) if warehouse else sales


MONEY = DecimalField(max_digits=18, decimal_places=2)
LINE = ExpressionWrapper(F('price') * F('quantity'), output_field=MONEY)
COST = ExpressionWrapper(F('quantity') * Coalesce('unit_cost', 'product__cost_price', output_field=MONEY), output_field=MONEY)


# ───────────────────────── Outils ─────────────────────────
def obtenir_ventes(ctx, args):
    """Chiffre d'affaires et nombre de commandes d'une période, par canal et dans le temps."""
    from orders.models import Order
    store, limit = ctx.scope('sales.view')
    start, end = _period(args)
    group_by = _choice(args, 'group_by', ['none', 'day', 'week', 'month'], 'none')
    wh = _warehouse(store, limit, _text(args, 'warehouse_code'))
    items = _paid_items(store, start, end, wh)
    direct = Q(order__shipping_address=Order.DIRECT_SALE_ADDRESS)
    agg = items.aggregate(online=Sum(LINE, filter=~direct), direct=Sum(LINE, filter=direct),
                          qty=Sum('quantity'), cost=Sum(COST), orders=Count('order', distinct=True),
                          no_cost=Count('id', filter=Q(unit_cost__isnull=True, product__cost_price__isnull=True)))
    from pos.services import NET_REVENUE
    pos = _pos_sales(store, start, end, wh).aggregate(net=Sum(NET_REVENUE), n=Count('id'))
    pos_rev = _money(pos['net'] or 0)
    revenue = _money(agg['online']) + _money(agg['direct']) + pos_rev
    result = {
        'periode': f'{start} au {end}', 'entrepot': wh.code if wh else 'tous',
        'chiffre_affaires_fcfa': revenue,
        'par_canal_fcfa': {'en_ligne': _money(agg['online']), 'vente_directe': _money(agg['direct']), 'caisse_pos': pos_rev},
        'commandes': agg['orders'] or 0, 'ventes_caisse_pos': pos['n'] or 0, 'articles_vendus': agg['qty'] or 0,
        'panier_moyen_fcfa': round(revenue / ((agg['orders'] or 0) + (pos['n'] or 0))) if (agg['orders'] or pos['n']) else 0,
        'cout_marchandises_fcfa_hors_pos': _money(agg['cost']),
        'lignes_sans_prix_achat': agg['no_cost'] or 0,
        'note': 'Commandes payées, hors annulées/remboursées ; caisse POS hors TVA.',
    }
    if group_by != 'none':
        trunc = {'day': TruncDay, 'week': TruncWeek, 'month': TruncMonth}[group_by]
        series = (items.annotate(p=trunc('order__created_at')).values('p')
                  .annotate(ca=Sum(LINE), commandes=Count('order', distinct=True)).order_by('p'))
        result['evolution'] = [{'periode': (row['p'].date() if hasattr(row['p'], 'date') else row['p']).isoformat(),
                                'ca_fcfa_hors_pos': _money(row['ca']), 'commandes': row['commandes']} for row in series][:120]
    return result


def meilleurs_produits(ctx, args):
    """Classement des produits vendus sur une période (CA, quantité ou marge)."""
    store, limit = ctx.scope('sales.view')
    start, end = _period(args)
    sort_by = _choice(args, 'sort_by', ['revenue', 'quantity', 'margin'], 'revenue')
    n = _int(args, 'limit', 10)
    wh = _warehouse(store, limit, _text(args, 'warehouse_code'))
    rows = (_paid_items(store, start, end, wh).values('product__name', 'product__sku')
            .annotate(qty=Sum('quantity'), revenue=Sum(LINE), cost=Sum(COST)))
    data = []
    for r in rows:
        revenue, cost = _money(r['revenue']), r['cost']
        margin = revenue - _money(cost) if cost is not None else None
        data.append({'produit': r['product__name'], 'sku': r['product__sku'], 'quantite': r['qty'], 'ca_fcfa': revenue,
                     'marge_fcfa': margin, 'marge_pct': round(margin * 100 / revenue) if margin is not None and revenue else None})
    key = {'revenue': lambda d: d['ca_fcfa'], 'quantity': lambda d: d['quantite'],
           'margin': lambda d: d['marge_fcfa'] if d['marge_fcfa'] is not None else -10**12}[sort_by]
    data.sort(key=key, reverse=True)
    return {'periode': f'{start} au {end}', 'tri': sort_by, 'produits': data[:n],
            'note': 'Marge = CA - prix d’achat figé à la vente (vide si le prix d’achat est inconnu). Hors caisse POS.'}


def rechercher_produits(ctx, args):
    """Catalogue de la boutique : prix, prix d'achat, marge, stock global et par entrepôt."""
    from catalog.models import Product
    store, limit = ctx.scope('products.view')
    query = _text(args, 'query')
    status = _choice(args, 'stock_status', ['all', 'low', 'out', 'archived'], 'all')
    n = _int(args, 'limit', 20)
    products = Product.objects.filter(store=store).prefetch_related('warehouse_stocks__warehouse')
    if query:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query))
    if status == 'low':
        products = products.filter(is_active=True, stock__gt=0, stock__lte=F('low_stock_threshold'))
    elif status == 'out':
        products = products.filter(is_active=True, stock=0)
    elif status == 'archived':
        products = products.filter(is_active=False)
    total = products.count()
    data = []
    for p in products.order_by('name')[:n]:
        stocks = [s for s in p.warehouse_stocks.all() if not limit or s.warehouse_id == limit]
        data.append({
            'produit': p.name, 'sku': p.sku, 'prix_fcfa': _money(p.price),
            'prix_achat_fcfa': _money(p.cost_price) if p.cost_price is not None else None, 'marge_pct': p.margin_percent,
            'stock_global': p.stock, 'seuil_alerte': p.low_stock_threshold, 'actif': p.is_active, 'deja_vendus': p.orders_count,
            'stock_par_entrepot': {s.warehouse.code: s.quantity for s in stocks},
        })
    return {'total_trouves': total, 'affiches': len(data), 'produits': data}


def etat_stock(ctx, args):
    """Synthèse du stock par entrepôt : produits, unités, valeur, ruptures et stocks faibles."""
    from inventory.models import ProductStock, Warehouse
    store, limit = ctx.scope('stock.view')
    wh = _warehouse(store, limit, _text(args, 'warehouse_code'))
    warehouses = [wh] if wh else list(Warehouse.objects.filter(store=store, is_active=True))
    out = []
    for w in warehouses:
        stocks = ProductStock.objects.filter(warehouse=w).select_related('product')
        agg = stocks.aggregate(units=Coalesce(Sum('quantity'), 0),
                               value=Sum(ExpressionWrapper(F('quantity') * F('product__price'), output_field=MONEY)))
        low = stocks.filter(quantity__gt=0, quantity__lte=F('product__low_stock_threshold'))
        rupture = stocks.filter(quantity=0)
        out.append({
            'entrepot': f'{w.name} ({w.code})', 'ville': w.city, 'produits': stocks.count(), 'unites': agg['units'],
            'valeur_prix_vente_fcfa': _money(agg['value']),
            'ruptures': [s.product.name for s in rupture[:15]], 'nb_ruptures': rupture.count(),
            'stock_faible': [f'{s.product.name} ({s.quantity}, seuil {s.product.low_stock_threshold})' for s in low[:15]],
            'nb_stock_faible': low.count(),
        })
    return {'entrepots': out}


def lister_entrepots(ctx, args):
    """Entrepôts de la boutique (codes à utiliser dans les autres outils)."""
    from inventory.models import Warehouse
    store, limit = ctx.scope('stock.view')
    warehouses = Warehouse.objects.filter(store=store)
    if limit:
        warehouses = warehouses.filter(pk=limit)
    return {'entrepots': [{'code': w.code, 'nom': w.name, 'ville': w.city, 'par_defaut': w.is_default, 'actif': w.is_active}
                          for w in warehouses]}


def lister_commandes(ctx, args):
    """Commandes contenant des produits de la boutique, avec le montant propre à la boutique."""
    from orders.models import Order, OrderItem
    store, limit = ctx.scope('orders.view')
    status = _choice(args, 'status', ['all'] + [c for c, _ in Order.STATUS_CHOICES], 'all')
    start, end = _period(args, default_days=90)
    n = _int(args, 'limit', 15)
    mine = OrderItem.objects.filter(store=store)
    if limit:
        mine = mine.filter(warehouse_id=limit)
    orders = Order.objects.filter(pk__in=mine.values('order'), created_at__date__gte=start, created_at__date__lte=end)
    if status != 'all':
        orders = orders.filter(status=status)
    counts = dict(orders.order_by().values_list('status').annotate(c=Count('id')))
    data = []
    for o in orders.select_related('buyer').order_by('-created_at')[:n]:
        part = mine.filter(order=o).aggregate(t=Sum(LINE))['t']
        data.append({'numero': o.order_number, 'date': timezone.localtime(o.created_at).strftime('%Y-%m-%d %H:%M'),
                     'client': o.shipping_name or o.buyer.display_name, 'ville': o.shipping_city, 'statut': o.get_status_display(),
                     'payee': o.is_paid, 'montant_boutique_fcfa': _money(part), 'vente_directe': o.is_direct_sale})
    return {'periode': f'{start} au {end}', 'total': orders.count(),
            'par_statut': {dict(Order.STATUS_CHOICES).get(k, k): v for k, v in counts.items()}, 'commandes': data}


def detail_commande(ctx, args):
    """Détail d'une commande (lignes de la boutique uniquement)."""
    from orders.models import Order
    store, limit = ctx.scope('orders.view')
    number = _text(args, 'order_number', 30).lstrip('#')
    if not number:
        raise ToolInputError('order_number est requis.')
    order = Order.objects.filter(order_number__iexact=number).select_related('buyer').first()
    items = order.items.filter(store=store).select_related('product', 'warehouse') if order else None
    if limit and items is not None:
        items = items.filter(warehouse_id=limit)
    if not order or not items.exists():
        raise ToolInputError(f'Commande {number} introuvable pour cette boutique.')
    return {
        'numero': order.order_number, 'statut': order.get_status_display(), 'payee': order.is_paid,
        'paiement': order.get_payment_method_display() if hasattr(order, 'get_payment_method_display') else order.payment_method,
        'date': timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M'),
        'client': order.shipping_name or order.buyer.display_name, 'telephone': order.shipping_phone,
        'ville': order.shipping_city, 'numero_suivi': order.tracking_number,
        'lignes': [{'produit': i.product.name, 'quantite': i.quantity, 'prix_unitaire_fcfa': _money(i.price),
                    'entrepot': i.warehouse.code if i.warehouse else None} for i in items],
        'montant_boutique_fcfa': _money(sum(i.price * i.quantity for i in items)),
        'total_commande_fcfa': _money(order.total_amount),
    }


def meilleurs_clients(ctx, args):
    """Clients qui ont le plus acheté dans la boutique sur une période."""
    store, limit = ctx.scope('customers.view')
    start, end = _period(args, default_days=365)
    n = _int(args, 'limit', 10)
    wh = _warehouse(store, limit, None) if limit else None
    rows = (_paid_items(store, start, end, wh)
            .values('order__buyer_id', 'order__buyer__first_name', 'order__buyer__last_name', 'order__buyer__username')
            .annotate(depense=Sum(LINE), commandes=Count('order', distinct=True)).order_by('-depense')[:n])
    return {'periode': f'{start} au {end}', 'clients': [{
        'client': f"{r['order__buyer__first_name']} {r['order__buyer__last_name']}".strip() or r['order__buyer__username'],
        'commandes': r['commandes'], 'depense_fcfa': _money(r['depense'])} for r in rows]}


def factures(ctx, args):
    """Factures de la boutique : montants encaissés, en attente et en retard."""
    from invoicing.models import Invoice
    store, limit = ctx.scope('invoicing.view')
    status = _choice(args, 'status', ['all', 'draft', 'sent', 'paid', 'cancelled', 'overdue'], 'all')
    n = _int(args, 'limit', 15)
    today = timezone.localdate()
    qs = Invoice.objects.filter(store=store)
    if limit:
        qs = qs.filter(warehouse_id=limit)
    agg = qs.aggregate(paye=Sum('total_amount', filter=Q(status='paid')), attente=Sum('total_amount', filter=Q(status='sent')),
                       retard=Sum('total_amount', filter=Q(status='sent', due_date__lt=today)),
                       nb_retard=Count('id', filter=Q(status='sent', due_date__lt=today)))
    if status == 'overdue':
        qs = qs.filter(status='sent', due_date__lt=today)
    elif status != 'all':
        qs = qs.filter(status=status)
    return {
        'encaisse_fcfa': _money(agg['paye']), 'en_attente_fcfa': _money(agg['attente']),
        'en_retard_fcfa': _money(agg['retard']), 'nb_en_retard': agg['nb_retard'],
        'factures': [{'numero': i.invoice_number, 'client': i.customer_name, 'statut': i.get_status_display(),
                      'montant_fcfa': _money(i.total_amount), 'emise_le': i.issue_date.isoformat() if i.issue_date else None,
                      'echeance': i.due_date.isoformat() if i.due_date else None}
                     for i in qs.order_by('-created_at')[:n]],
    }


def comptabilite_entrepot(ctx, args):
    """Compte de résultat mensuel d'un entrepôt (revenus, coût des marchandises, commissions, dépenses)."""
    from dashboard.views import _warehouse_month_figures
    store, limit = ctx.scope('finances.view')
    wh = _warehouse(store, limit, _text(args, 'warehouse_code'))
    if wh is None:
        from inventory.models import Warehouse
        wh = Warehouse.objects.filter(store=store, is_default=True).first()
        if wh is None:
            raise ToolInputError('Aucun entrepôt : précisez warehouse_code.')
    today = timezone.localdate()
    year = _int(args, 'year', today.year, 2000, 2100)
    month = _int(args, 'month', today.month, 1, 12)
    f = _warehouse_month_figures(wh, year, month)
    return {'entrepot': f'{wh.name} ({wh.code})', 'mois': f'{year}-{month:02d}', **{
        'revenus_fcfa': f['revenue'], 'dont_en_ligne': f['online'], 'dont_ventes_directes': f['direct'],
        'dont_caisse_pos': f['pos'], 'cout_marchandises_fcfa': f['cogs'], 'marge_brute_fcfa': f['gross'],
        'commissions_fcfa': f['commission'], 'depenses_fcfa': f['expenses'], 'resultat_net_fcfa': f['net'],
        'lignes_sans_prix_achat': f['missing_cost']}}


def marketing(ctx, args):
    """Codes promo, campagnes, fidélité et emails de la boutique."""
    from marketing.models import Campaign, LoyaltyProgram, Newsletter, PromoCode
    store, _ = ctx.scope(None)
    now = timezone.now()
    promos = PromoCode.objects.filter(store=store)
    return {
        'codes_promo': [{'code': p.code, 'reduction': f"{int(p.discount_value)}{' %' if p.discount_type == 'percentage' else ' FCFA'}",
                         'utilisations': p.usage_count, 'limite': p.usage_limit, 'valide': p.is_valid,
                         'fin': timezone.localtime(p.valid_to).date().isoformat()} for p in promos.order_by('-created_at')[:15]],
        'campagnes': [{'nom': c.name, 'statut': c.get_status_display(), 'clics': c.clicks_count, 'conversions': c.conversions_count,
                       'ca_genere_fcfa': _money(c.revenue_generated), 'budget_fcfa': _money(c.budget)}
                      for c in Campaign.objects.filter(store=store).order_by('-created_at')[:10]],
        'membres_fidelite': LoyaltyProgram.objects.filter(store=store).count(),
        'emails_envoyes_90j': Newsletter.objects.filter(store=store, status='sent', sent_at__gte=now - timedelta(days=90)).count(),
    }


def devis_ouverts(ctx, args):
    """Demandes de devis (RFQ) ouvertes auxquelles la boutique n'a pas encore répondu."""
    from orders.models import RFQ
    store, _ = ctx.scope('orders.view')
    n = _int(args, 'limit', 10)
    rfqs = (RFQ.objects.filter(status__in=('open', 'quoted')).exclude(quotes__store=store).exclude(buyer=store.owner)
            .select_related('category').order_by('-created_at'))
    return {'total': rfqs.count(), 'demandes': [{
        'id': r.pk, 'produit': r.product_name, 'quantite': f'{r.quantity} {r.unit}', 'categorie': r.category.name if r.category else None,
        'prix_cible_fcfa': _money(r.target_price) if r.target_price else None,
        'date': timezone.localtime(r.created_at).date().isoformat()} for r in rfqs[:n]]}


def whatsapp(ctx, args):
    """Activité WhatsApp de la boutique : messages envoyés, lecture, échecs, conversations non lues."""
    from whatsapp import services as wa
    from whatsapp.models import WhatsAppInstance
    store, _ = ctx.scope('orders.manage')
    inst = WhatsAppInstance.objects.filter(store=store).first()
    stats = wa.activity_stats(inst)
    unread = inst.chats.filter(unread_count__gt=0).select_related('customer')[:10] if inst else []
    return {'connecte': bool(inst and inst.is_connected), 'numero': f'+{inst.phone}' if inst and inst.phone else None,
            'stats_30j': stats, 'conversations_non_lues': [{'contact': c.display_name, 'non_lus': c.unread_count,
                                                             'dernier_message': c.last_preview} for c in unread]}


# ───────────────────────── Registre ─────────────────────────
_PERIOD_PROPS = {
    'start_date': {'type': 'string', 'description': 'Début de période, format AAAA-MM-JJ (défaut : 30 jours avant end_date).'},
    'end_date': {'type': 'string', 'description': "Fin de période incluse, format AAAA-MM-JJ (défaut : aujourd'hui)."},
}
_WH = {'warehouse_code': {'type': 'string', 'description': "Code d'un entrepôt (voir lister_entrepots). Vide = tous."}}
_LIMIT = {'limit': {'type': 'integer', 'description': f'Nombre maximum de lignes (1 à {MAX_LIMIT}).'}}

TOOL_SPECS = [
    (obtenir_ventes, {**_PERIOD_PROPS, **_WH,
                      'group_by': {'type': 'string', 'enum': ['none', 'day', 'week', 'month'],
                                   'description': "Détail dans le temps (none par défaut)."}}),
    (meilleurs_produits, {**_PERIOD_PROPS, **_WH, **_LIMIT,
                          'sort_by': {'type': 'string', 'enum': ['revenue', 'quantity', 'margin'], 'description': 'Critère de tri.'}}),
    (rechercher_produits, {'query': {'type': 'string', 'description': 'Texte cherché dans le nom ou le SKU (vide = tous).'},
                           'stock_status': {'type': 'string', 'enum': ['all', 'low', 'out', 'archived']}, **_LIMIT}),
    (etat_stock, {**_WH}),
    (lister_entrepots, {}),
    (lister_commandes, {**_PERIOD_PROPS, **_LIMIT,
                        'status': {'type': 'string', 'enum': ['all', 'pending', 'confirmed', 'processing', 'shipped',
                                                              'delivered', 'cancelled', 'refunded']}}),
    (detail_commande, {'order_number': {'type': 'string', 'description': 'Numéro de commande, ex. ALB-1A2B3C4D.'}}),
    (meilleurs_clients, {**_PERIOD_PROPS, **_LIMIT}),
    (factures, {'status': {'type': 'string', 'enum': ['all', 'draft', 'sent', 'paid', 'cancelled', 'overdue']}, **_LIMIT}),
    (comptabilite_entrepot, {**_WH, 'year': {'type': 'integer'}, 'month': {'type': 'integer', 'description': '1 à 12'}}),
    (marketing, {}),
    (devis_ouverts, {**_LIMIT}),
    (whatsapp, {}),
]

TOOLS = [
    {'name': fn.__name__, 'description': (fn.__doc__ or '').strip(),
     'input_schema': {'type': 'object', 'properties': props, 'additionalProperties': False}}
    for fn, props in TOOL_SPECS
]
HANDLERS = {fn.__name__: fn for fn, _ in TOOL_SPECS}
TOOL_LABELS = {
    'obtenir_ventes': 'Ventes', 'meilleurs_produits': 'Top produits', 'rechercher_produits': 'Catalogue',
    'etat_stock': 'Stock', 'lister_entrepots': 'Entrepôts', 'lister_commandes': 'Commandes', 'detail_commande': 'Commande',
    'meilleurs_clients': 'Clients', 'factures': 'Factures', 'comptabilite_entrepot': 'Comptabilité',
    'marketing': 'Marketing', 'devis_ouverts': 'Devis', 'whatsapp': 'WhatsApp',
}


def run_tool(ctx, name, args):
    """Exécute un outil. Retourne (texte JSON, est_erreur). Ne lève jamais d'exception vers la boucle."""
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({'erreur': f'Outil inconnu : {name}'}, ensure_ascii=False), True
    if not isinstance(args, dict):
        return json.dumps({'erreur': 'Arguments invalides (objet JSON attendu).'}, ensure_ascii=False), True
    unknown = set(args) - set(next(t for t in TOOLS if t['name'] == name)['input_schema']['properties'])
    if unknown:
        return json.dumps({'erreur': f"Arguments inconnus : {', '.join(sorted(unknown))}"}, ensure_ascii=False), True
    try:
        result = handler(ctx, args)
    except ToolInputError as exc:
        return json.dumps({'erreur': str(exc)}, ensure_ascii=False), True
    except Exception:  # bogue ou donnée inattendue : on le journalise, le modèle reçoit un échec propre
        import logging
        logging.getLogger(__name__).exception("Outil assistant %s en erreur", name)
        return json.dumps({'erreur': "Erreur interne pendant la lecture des données."}, ensure_ascii=False), True
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + ' …[résultat tronqué : réduisez la période ou le paramètre limit]'
    return text, False
