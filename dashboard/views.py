from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages as django_messages
from django.db.models import Sum, Count, F, Q, Avg
from django.db.models.functions import TruncMonth, TruncDay
from django.utils import timezone
from datetime import timedelta
from orders.models import Order, OrderItem
from catalog.models import Product, Category, Review
from accounts.models import User
from store.models import Store
import json
import re
from messaging.models import Conversation, Message
from django.http import JsonResponse
from django.utils.text import slugify
from django.urls import reverse
from functools import wraps
from django.conf import settings
from store import access


def seller_or_admin_required(view_func):
    """Bloque l'accès aux acheteurs : réservé aux vendeurs (avec boutique) et admins."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        is_admin = user.is_superuser or user.role == 'admin'
        is_seller = user.is_seller and user.stores.exists()
        is_member = user.store_memberships.filter(is_active=True).exists()
        if not (is_admin or is_seller or is_member):
            django_messages.error(request, "Accès réservé aux vendeurs et administrateurs.")
            return redirect('dashboard:index')
        return view_func(request, *args, **kwargs)
    return wrapper


def store_permission_required(permission):
    """Vérifie qu'un employé a la permission requise. Le propriétaire a tous les droits."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if user.is_superuser or user.role == 'admin' or user.stores.exists():
                return view_func(request, *args, **kwargs)
            member = user.store_memberships.filter(is_active=True).first()
            if member and member.has_permission(permission):
                return view_func(request, *args, **kwargs)
            django_messages.error(request, "Vous n'avez pas cette permission.")
            return redirect('dashboard:index')
        return wrapper
    return decorator


DASHBOARD_PERIODS = [('7', '7 jours'), ('30', '30 jours'), ('90', '90 jours'), ('365', '12 mois')]
MONTHS_SHORT_FR = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.']
DAYS_SHORT_FR = ['lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.', 'dim.']


def _period_figures(store, warehouse_id, start, end):
    """Chiffres d'une période : ventes payées (hors annulées/remboursées) + caisse POS, de la boutique (ou de toutes : store=None)."""
    from django.db.models import DecimalField, ExpressionWrapper
    from django.db.models.functions import Coalesce
    from pos.models import POSSale
    money = DecimalField(max_digits=18, decimal_places=2)
    line = ExpressionWrapper(F('price') * F('quantity'), output_field=money)
    cost = ExpressionWrapper(F('quantity') * Coalesce('unit_cost', 'product__cost_price', output_field=money), output_field=money)
    items = (OrderItem.objects.filter(order__is_paid=True, order__created_at__date__gte=start, order__created_at__date__lte=end)
             .exclude(order__status__in=['cancelled', 'refunded']))
    pos = POSSale.objects.filter(status='completed', created_at__date__gte=start, created_at__date__lte=end)
    if store is not None:
        items, pos = items.filter(store=store), pos.filter(store=store)
    if warehouse_id:
        items, pos = items.filter(warehouse_id=warehouse_id), pos.filter(warehouse_id=warehouse_id)
    direct = Q(order__shipping_address=Order.DIRECT_SALE_ADDRESS)
    agg = items.aggregate(online=Sum(line, filter=~direct), direct=Sum(line, filter=direct), cost=Sum(cost),
                          costed=Sum(line, filter=Q(unit_cost__isnull=False) | Q(product__cost_price__isnull=False)),
                          orders=Count('order', distinct=True), qty=Sum('quantity'))
    from pos.services import NET_REVENUE
    pos_agg = pos.aggregate(net=Sum(NET_REVENUE), n=Count('id'))
    pos_rev = int(pos_agg['net'] or 0)
    online, direct_rev = int(agg['online'] or 0), int(agg['direct'] or 0)
    revenue = online + direct_rev + pos_rev
    sales = (agg['orders'] or 0) + (pos_agg['n'] or 0)
    costed = int(agg['costed'] or 0)
    return {
        'items': items, 'pos': pos, 'line': line,
        'revenue': revenue, 'online': online, 'direct': direct_rev, 'pos_revenue': pos_rev,
        'orders': agg['orders'] or 0, 'pos_count': pos_agg['n'] or 0, 'sales': sales, 'qty': agg['qty'] or 0,
        'basket': round(revenue / sales) if sales else 0,
        # Marge calculée seulement sur les lignes dont le prix d'achat est connu
        'margin': costed - int(agg['cost'] or 0) if costed else None,
        'margin_rate': round((costed - int(agg['cost'] or 0)) * 100 / costed) if costed else None,
        'cost_coverage': round(costed * 100 / (online + direct_rev)) if (online + direct_rev) else 0,
    }


def _variation_pct(current, previous):
    if previous in (None, 0) or current is None:
        return None
    return round((current - previous) * 100 / abs(previous))


@login_required
def index(request):
    user = request.user
    is_admin = access.is_admin(user)
    store = access.acting_store(user)
    if not is_admin and store is None:
        django_messages.error(request, "Le tableau de bord est réservé aux vendeurs.")
        return redirect('home')
    scope_store = None if is_admin and store is None else store
    limit = access.member_warehouse_id(user, store) if store is not None else None
    can = lambda perm: is_admin or (store is not None and access.has_perm(user, store, perm))  # noqa: E731
    perms = {k: can(v) for k, v in {'sales': 'sales.view', 'orders': 'orders.view', 'stock': 'stock.view',
                                     'invoices': 'invoicing.view', 'products': 'products.view',
                                     'manage': 'orders.manage', 'sell': 'sales.create'}.items()}
    perms['owner'] = can(None)

    # ── Période ──
    today = timezone.localdate()
    period = request.GET.get('period', '30')
    if period not in dict(DASHBOARD_PERIODS):
        period = '30'
    days = int(period)
    start, end = today - timedelta(days=days - 1), today
    prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)

    ctx = {
        'is_admin': is_admin, 'store': store, 'perms': perms, 'period': period, 'periods': DASHBOARD_PERIODS,
        'period_label': dict(DASHBOARD_PERIODS)[period], 'today': today, 'start': start,
        'day_name': ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'][today.weekday()],
    }

    # ── Ventes (si l'utilisateur a le droit de les voir) ──
    if perms['sales']:
        cur = _period_figures(scope_store, limit, start, end)
        prev = _period_figures(scope_store, limit, prev_start, prev_end)
        ctx.update({'cur': cur, 'prev': prev, 'var': {
            'revenue': _variation_pct(cur['revenue'], prev['revenue']),
            'sales': _variation_pct(cur['sales'], prev['sales']),
            'basket': _variation_pct(cur['basket'], prev['basket']),
            'margin': _variation_pct(cur['margin'], prev['margin']),
        }})
        # Courbe : par jour (≤ 90 jours) ou par mois (12 mois), comparée à la période précédente
        def series(fig, s_start, s_end, monthly):
            trunc = TruncMonth if monthly else TruncDay
            points = {}
            for row in fig['items'].annotate(p=trunc('order__created_at')).values('p').annotate(t=Sum(fig['line'])):
                key = row['p'].date() if hasattr(row['p'], 'date') else row['p']
                points[key.replace(day=1) if monthly else key] = points.get(key, 0) + int(row['t'] or 0)
            for row in fig['pos'].annotate(p=trunc('created_at')).values('p').annotate(t=Sum('total_amount'), x=Sum('tax_amount')):
                key = row['p'].date() if hasattr(row['p'], 'date') else row['p']
                key = key.replace(day=1) if monthly else key
                points[key] = points.get(key, 0) + int((row['t'] or 0) - (row['x'] or 0))
            labels, values, d = [], [], (s_start.replace(day=1) if monthly else s_start)
            while d <= s_end:
                labels.append(f'{MONTHS_SHORT_FR[d.month - 1]} {str(d.year)[2:]}' if monthly else f'{DAYS_SHORT_FR[d.weekday()]} {d.day}')
                values.append(points.get(d, 0))
                d = (d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1) if monthly else d + timedelta(days=1))
            return labels, values
        monthly = days > 90
        labels, values = series(cur, start, end, monthly)
        _, prev_values = series(prev, prev_start, prev_end, monthly)
        ctx['chart'] = {'labels': labels, 'current': values, 'previous': prev_values[-len(values):] if prev_values else [],
                        'monthly': monthly}
        ctx['spark'] = values[-14:]
        ctx['channels'] = [(label, value, round(value * 100 / cur['revenue']) if cur['revenue'] else 0, color)
                           for label, value, color in (('En ligne', cur['online'], 'var(--dash-blue)'),
                                                       ('Ventes directes', cur['direct'], 'var(--dash-purple)'),
                                                       ('Caisse POS', cur['pos_revenue'], 'var(--dash-green)'))]
        top = (cur['items'].values('product__name').annotate(rev=Sum(cur['line']), qty=Sum('quantity')).order_by('-rev')[:5])
        best = max([int(t['rev'] or 0) for t in top] + [1])
        ctx['top_products'] = [{'name': t['product__name'], 'qty': t['qty'], 'rev': int(t['rev'] or 0),
                                'pct': round(int(t['rev'] or 0) * 100 / best)} for t in top]
        # Nouveaux clients : premier achat (payé) dans la boutique pendant la période
        from django.db.models import Min
        firsts = OrderItem.objects.filter(order__is_paid=True)
        if scope_store is not None:
            firsts = firsts.filter(store=scope_store)
        ctx['new_customers'] = (firsts.values('order__buyer').annotate(first=Min('order__created_at'))
                                .filter(first__date__gte=start, first__date__lte=end).count())

    # ── Commandes ──
    if perms['orders']:
        mine = OrderItem.objects.all() if scope_store is None else OrderItem.objects.filter(store=scope_store)
        if limit:
            mine = mine.filter(warehouse_id=limit)
        orders = Order.objects.filter(pk__in=mine.values('order'))
        recent = list(orders.select_related('buyer').order_by('-created_at')[:7])
        shares = dict(mine.filter(order__in=recent).values('order').annotate(t=Sum(F('price') * F('quantity'))).values_list('order', 't'))
        for o in recent:
            o.share = int(shares.get(o.pk) or 0)
        status_rows = (orders.filter(created_at__date__gte=start).values('status').annotate(n=Count('id')).order_by('-n'))
        ctx.update({
            'recent_orders': recent,
            'status_chart': {'labels': [dict(Order.STATUS_CHOICES).get(r['status'], r['status']) for r in status_rows],
                             'values': [r['n'] for r in status_rows]},
            'period_orders': sum(r['n'] for r in status_rows),
        })

    # ── À faire ──
    todo = []
    if perms['orders']:
        pending = orders.filter(status='pending')
        n = pending.count()
        if n:
            oldest = pending.order_by('created_at').first()
            age = (timezone.now() - oldest.created_at).days
            todo.append(('fa-box', 'var(--dash-yellow)', f'{n} commande{"s" if n > 1 else ""} en attente',
                         f'La plus ancienne date de {age} jour{"s" if age > 1 else ""}' if age else "Reçue aujourd'hui",
                         reverse('dashboard:orders') + '?status=pending'))
        to_ship = orders.filter(status__in=['confirmed', 'processing']).count()
        if to_ship:
            todo.append(('fa-truck-fast', 'var(--dash-blue)', f'{to_ship} commande{"s" if to_ship > 1 else ""} à expédier',
                         'Confirmées ou en préparation', reverse('dashboard:orders') + '?status=processing'))
    if perms['stock'] and scope_store is not None:
        from inventory.models import ProductStock
        stocks = ProductStock.objects.filter(warehouse__store=scope_store, product__is_active=True)
        if limit:
            stocks = stocks.filter(warehouse_id=limit)
        out = stocks.filter(quantity=0).count()
        low = stocks.filter(quantity__gt=0, quantity__lte=F('product__low_stock_threshold')).count()
        if out or low:
            todo.append(('fa-boxes-stacked', 'var(--dash-red)' if out else 'var(--dash-yellow)',
                         ' · '.join(x for x in (f'{out} en rupture' if out else '', f'{low} en stock faible' if low else '') if x),
                         'Seuil d\'alerte de chaque produit, par entrepôt', reverse('dashboard:products') + '?stock=low'))
    if perms['invoices'] and scope_store is not None:
        from invoicing.models import Invoice
        inv = Invoice.objects.filter(store=scope_store, status='sent', due_date__lt=today)
        if limit:
            inv = inv.filter(warehouse_id=limit)
        agg = inv.aggregate(n=Count('id'), t=Sum('total_amount'))
        if agg['n']:
            todo.append(('fa-file-invoice-dollar', 'var(--dash-red)', f'{agg["n"]} facture{"s" if agg["n"] > 1 else ""} en retard',
                         f'{int(agg["t"] or 0):,} F à relancer'.replace(',', ' '), reverse('invoicing:invoices') + '?status=overdue'))
    if perms['orders'] and scope_store is not None:
        from orders.models import RFQ
        rfqs = RFQ.objects.filter(status__in=('open', 'quoted')).exclude(quotes__store=scope_store).exclude(buyer=scope_store.owner).count()
        if rfqs:
            todo.append(('fa-file-signature', 'var(--dash-purple)', f'{rfqs} demande{"s" if rfqs > 1 else ""} de devis sans réponse',
                         'Répondez vite pour décrocher la vente', reverse('dashboard:rfqs')))
    if perms['manage'] and scope_store is not None:
        from django.db.models import Sum as _Sum
        from whatsapp.models import WhatsAppChat
        unread = WhatsAppChat.objects.filter(instance__store=scope_store).aggregate(t=_Sum('unread_count'))['t'] or 0
        if unread:
            todo.append(('fa-comments', '#128C7E', f'{unread} message{"s" if unread > 1 else ""} WhatsApp non lu{"s" if unread > 1 else ""}',
                         'Vos clients attendent une réponse', reverse('whatsapp:inbox')))
    if perms['products'] and scope_store is not None:
        no_cost = scope_store.products.filter(is_active=True, cost_price__isnull=True).count()
        if no_cost:
            todo.append(('fa-tags', 'var(--dash-text3)', f'{no_cost} produit{"s" if no_cost > 1 else ""} sans prix d\'achat',
                         'Renseignez-le pour suivre votre marge', reverse('dashboard:products')))
    ctx['todo'] = todo

    # ── Plateforme (admin) ──
    if is_admin:
        month_ago = timezone.now() - timedelta(days=30)
        ctx['platform'] = {
            'users': User.objects.count(), 'new_users': User.objects.filter(date_joined__gte=month_ago).count(),
            'sellers': User.objects.filter(role='seller').count(), 'buyers': User.objects.filter(role='buyer').count(),
            'stores': Store.objects.count(), 'verified': Store.objects.filter(is_verified=True).count(),
        }
    if store is not None and not is_admin:
        from billing.payments import store_methods
        ctx['no_payment'] = not store_methods(store)
    return render(request, 'dashboard/index.html', ctx)


@login_required
@seller_or_admin_required
def dash_orders(request):
    if request.user.is_seller and (request.user.store is not None):
        orders = Order.objects.filter(items__store=request.user.store).distinct()
    else:
        orders = Order.objects.all()
    status = request.GET.get('status')
    if status: orders = orders.filter(status=status)
    q = request.GET.get('q', '').strip()
    if q:
        orders = orders.filter(Q(order_number__icontains=q) | Q(buyer__username__icontains=q) | Q(buyer__first_name__icontains=q) | Q(buyer__last_name__icontains=q))

    # Filtres date
    from datetime import datetime
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    if date_from:
        try:
            orders = orders.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            orders = orders.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    total_revenue = OrderItem.objects.filter(order__in=orders, order__is_paid=True).aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0
    return render(request, 'dashboard/orders.html', {
        'orders': orders.select_related('buyer').order_by('-created_at'),
        'status_choices': Order.STATUS_CHOICES,
        'current_status': status,
        'search_query': q,
        'date_from': date_from,
        'date_to': date_to,
        'total': orders.count(),
        'pending': orders.filter(status='pending').count(),
        'delivered': orders.filter(status='delivered').count(),
        'total_revenue': total_revenue,
    })


def _credit_sellers_for_order(order):
    """Crédite une seule fois chaque vendeur de la commande (montant de SES lignes, moins la commission).
    La transaction 'sale' par (commande, vendeur) sert de verrou : si elle existe déjà, rien n'est recrédité."""
    from accounting.models import Transaction, SellerWallet
    from django.conf import settings
    per_owner = {}
    for item in order.items.select_related('store__owner'):
        per_owner.setdefault(item.store.owner, 0)
        per_owner[item.store.owner] += int(item.price) * item.quantity
    for owner, sale_amount in per_owner.items():
        commission = int(sale_amount * settings.SALES_COMMISSION_RATE)
        _, created = Transaction.objects.get_or_create(
            order=order, user=owner, type='sale',
            defaults={
                'amount': sale_amount,
                'commission_amount': commission,
                'net_amount': sale_amount - commission,
                'status': 'completed',
                'reference': f'Vente #{order.order_number}',
            }
        )
        if created:
            wallet, _ = SellerWallet.objects.get_or_create(user=owner)
            wallet.balance += (sale_amount - commission)
            wallet.total_earned += sale_amount
            wallet.total_commission_paid += commission
            wallet.save()


@login_required
@seller_or_admin_required
def dash_order_detail(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    # Un vendeur (ou un employé avec orders.view) ne voit que les commandes contenant ses produits
    allowed, store, limit = access.page_scope(request.user, 'orders.view')
    if store is not None:
        mine = order.items.filter(store=store)
        if limit:
            mine = mine.filter(warehouse_id=limit)
        allowed = allowed and mine.exists()
    if not allowed:
        django_messages.error(request, "Cette commande ne concerne pas votre boutique.")
        return redirect('dashboard:orders')
    if request.method == 'POST':
        if store is not None and not access.has_perm(request.user, store, 'orders.manage'):
            django_messages.error(request, "Vous n'avez pas le droit de modifier cette commande.")
            return redirect('dashboard:order_detail', order_number=order_number)
        new_status = request.POST.get('status')
        from orders.services import STOPPED, apply_cancellation
        if new_status and new_status != order.status and order.status in STOPPED and new_status != 'refunded':
            # Le stock, la fidélité et le crédit vendeur ont été défaits : pas de retour en arrière
            django_messages.error(request, "Une commande annulée ne peut pas être réactivée. Le client peut repasser commande.")
            return redirect('dashboard:order_detail', order_number=order_number)
        if new_status and new_status in dict(Order.STATUS_CHOICES):
            from django.db import transaction as db_transaction
            from orders.models import OrderStatusEvent
            per_store = {}
            with db_transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order.pk)
                changed = order.status != new_status
                if new_status == 'cancelled' and changed:
                    reason = (request.POST.get('cancel_reason') or '').strip()
                    per_store = apply_cancellation(order, request.user, reason)
                else:
                    order.status = new_status
                    if new_status in ['confirmed', 'delivered']:
                        order.is_paid = True
                        _credit_sellers_for_order(order)
                    order.save()
                    if changed:
                        OrderStatusEvent.objects.create(order=order, status=new_status, by=request.user)
            # Les autres boutiques de la commande sont prévenues de l'annulation
            if per_store:
                from messaging.utils import notify_store
                who = store.name if store is not None else 'Comptoir'
                for st in per_store:
                    if store is None or st.pk != store.pk:
                        notify_store(st, 'orders.view', 'order', f'Commande {order.order_number} annulée',
                                     f'La commande a été annulée par {who}. Le stock de vos articles a été remis en place.',
                                     url=f'/dashboard/commandes/{order.order_number}/')
            # Notifier le client du changement de statut
            from messaging.utils import notify
            status_labels = {
                'confirmed': 'confirmée', 'processing': 'en préparation',
                'shipped': 'expédiée', 'delivered': 'livrée', 'cancelled': 'annulée',
            }
            label = status_labels.get(new_status)
            if label:
                notify(
                    order.buyer, 'order',
                    f'Commande {order.order_number} {label}',
                    f'Votre commande de {order.total_amount:.0f} FCFA est maintenant {label}.',
                    url=f'/commandes/{order.order_number}/',
                    send_email=True,
                )
            from whatsapp.services import notify_order_status
            notify_order_status(order, new_status, store)
            django_messages.success(request, f'Statut mis à jour.')
        tracking = request.POST.get('tracking_number')
        if tracking:
            order.tracking_number = tracking
            order.save()
        return redirect('dashboard:order_detail', order_number=order_number)
    return render(request, 'dashboard/order_detail.html', {'order': order})


def get_user_warehouse(request):
    """Retourne l'entrepôt de l'employé connecté, ou None pour le propriétaire/admin."""
    member = request.user.store_memberships.filter(is_active=True).first()
    return member.warehouse if member and member.warehouse else None


def _products_queryset(request):
    """Produits visibles + filtres GET (page Produits et son export).
    Retourne None si l'accès est refusé, sinon un dict (queryset filtré, base des stats, contexte)."""
    from django.db.models import OuterRef, Subquery
    from inventory.models import ProductStock
    allowed, store, limit = access.page_scope(request.user, 'products.view')
    if not allowed:
        return None
    products = Product.objects.all() if store is None else store.products.all()
    warehouse, invalid = access.resolve_warehouse(request, store, limit)
    if invalid:
        products = products.none()
    stock_field = 'stock'
    if warehouse:
        # Stock de l'entrepôt affiché, à côté du stock global
        here = ProductStock.objects.filter(product=OuterRef('pk'), warehouse=warehouse).values('quantity')[:1]
        products = products.filter(warehouse_stocks__warehouse=warehouse).annotate(stock_here=Subquery(here))
        stock_field = 'stock_here'

    q = request.GET.get('q', '').strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    base = products
    stock_filter = request.GET.get('stock', '')
    if stock_filter == 'out':
        products = products.filter(**{stock_field: 0})
    elif stock_filter == 'low':
        products = products.filter(**{f'{stock_field}__gt': 0, f'{stock_field}__lte': F('low_stock_threshold')})
    elif stock_filter == 'archived':
        products = products.filter(is_active=False)
    return {
        'products': products.select_related('category', 'store').order_by('-created_at', '-pk'),
        'base': base,
        'store': store,
        'limit': limit,
        'warehouse': warehouse,
        'stock_field': stock_field,
        'search_query': q,
        'stock_filter': stock_filter,
    }


@login_required
@seller_or_admin_required
def dash_products(request):
    from django.core.paginator import Paginator
    data = _products_queryset(request)
    if data is None:
        django_messages.error(request, "Vous n'avez pas accès aux produits.")
        return redirect('dashboard:index')
    sf, store, warehouse = data['stock_field'], data['store'], data['warehouse']
    stats = data['base'].aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True)),
        low=Count('id', filter=Q(**{f'{sf}__gt': 0, f'{sf}__lte': F('low_stock_threshold')})),
        out=Count('id', filter=Q(**{sf: 0})),
        featured=Count('id', filter=Q(is_featured=True)),
    )
    page_obj = Paginator(data['products'], 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    can_edit = store is None or access.has_perm(request.user, store, None)
    can_adjust = store is None or access.stock_access(request.user, store, 'stock.adjust', warehouse)
    return render(request, 'dashboard/products.html', {
        'products': page_obj,
        'page_obj': page_obj,
        'categories': Category.objects.filter(is_active=True),
        'search_query': data['search_query'],
        'stock_filter': data['stock_filter'],
        'warehouse': warehouse,
        'current_warehouse': warehouse,
        'warehouses': access.selectable_warehouses(store, data['limit']),
        'wh_query': f'warehouse={warehouse.pk}&' if warehouse else '',
        'base_query': params.urlencode(),
        'stats': stats,
        'total': stats['total'],
        'active': stats['active'],
        'low': stats['low'],
        'featured': stats['featured'],
        'can_edit': can_edit,
        'can_adjust': can_adjust,
    })


@login_required
@seller_or_admin_required
def dash_products_export(request):
    """Export Excel des produits (mêmes filtres que la page)"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    data = _products_queryset(request)
    if data is None:
        django_messages.error(request, "Vous n'avez pas accès aux produits.")
        return redirect('dashboard:index')
    warehouse = data['warehouse']
    wb = Workbook()
    ws = wb.active
    ws.title = 'Produits'
    headers = ['Produit', 'SKU', 'Catégorie', 'Prix (F)', "Prix d'achat (F)", 'Marge (%)', 'Stock global']
    if warehouse:
        headers.append(f'Stock {warehouse.code}')
    headers += ['Seuil', 'Ventes', 'Statut']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for p in data['products'][:5000]:
        row = [p.name, p.sku, p.category.name if p.category else '', int(p.price),
               int(p.cost_price) if p.cost_price is not None else '', p.margin_percent if p.margin_percent is not None else '', p.stock]
        if warehouse:
            row.append(p.stock_here or 0)
        row += [p.low_stock_threshold, p.orders_count, 'Actif' if p.is_active else 'Archivé']
        ws.append(row)
    for col, width in zip('ABCDEFGHIJK', (38, 14, 20, 12, 14, 10, 12, 12, 8, 8, 10)):
        ws.column_dimensions[col].width = width
    suffix = f'_{warehouse.code}' if warehouse else ''
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="produits{suffix}_{timezone.localdate():%Y%m%d}.xlsx"'
    wb.save(response)
    return response


def _non_negative_or_none(value):
    """Entier >= 0 saisi dans un formulaire, ou None si vide / invalide."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


@login_required
@seller_or_admin_required
def dash_product_edit(request, pk=None):
    product = get_object_or_404(Product, pk=pk) if pk else None
    is_admin = request.user.is_superuser or request.user.role == 'admin'
    # Un vendeur ne peut modifier que ses propres produits
    if product and not is_admin and product.store != request.user.store:
        django_messages.error(request, "Vous ne pouvez pas modifier ce produit.")
        return redirect('dashboard:products')
    categories = Category.objects.all()
    store = getattr(request.user, 'store', None) or (Store.objects.first() if is_admin else None)
    if store is None:
        django_messages.error(request, "Aucune boutique associée.")
        return redirect('dashboard:index')
    if request.method == 'POST':
        data = {
            'name': request.POST.get('name'),
            'description': request.POST.get('description'),
            'price': request.POST.get('price'),
            'old_price': request.POST.get('old_price') or None,
            'cost_price': _non_negative_or_none(request.POST.get('cost_price')),
            'stock': request.POST.get('stock', 0),
            'min_order': request.POST.get('min_order', 1),
            'colors': request.POST.get('colors', ''),
            'sizes': request.POST.get('sizes', ''),
            'specifications': request.POST.get('specifications', ''),
            'origin': request.POST.get('origin', 'Cameroun'),
            'is_active': 'is_active' in request.POST,
            'is_featured': 'is_featured' in request.POST,
            'is_flash_deal': 'is_flash_deal' in request.POST,
        }
        cat_id = request.POST.get('category')
        if cat_id: data['category'] = Category.objects.get(pk=cat_id)
        if product:
            # Le stock ne se modifie que via l'ajustement (traçabilité)
            data.pop('stock', None)
            for k, v in data.items(): setattr(product, k, v)
            for f in ['image', 'image_2', 'image_3', 'image_4']:
                if request.FILES.get(f): setattr(product, f, request.FILES[f])
            product.save()
        else:
            data['store'] = store
            product = Product(**data)
            for f in ['image', 'image_2', 'image_3', 'image_4']:
                if request.FILES.get(f): setattr(product, f, request.FILES[f])
            product.save()
        django_messages.success(request, 'Produit sauvegardé !')
        return redirect('dashboard:products')
    return render(request, 'dashboard/product_edit.html', {'product': product, 'categories': categories})


@login_required
@seller_or_admin_required
def dash_product_delete(request, pk):
    """Archive un produit (style Odoo : on ne supprime pas, on archive).
    La suppression définitive n'est possible que si le produit n'a aucun historique."""
    product = get_object_or_404(Product, pk=pk)
    is_admin = request.user.is_superuser or request.user.role == 'admin'
    if not is_admin and product.store != request.user.store:
        django_messages.error(request, 'Vous ne pouvez pas modifier ce produit.')
        return redirect('dashboard:products')
    if request.method == 'POST':
        has_history = product.stock_movements.exists() or product.orderitem_set.exists()
        if has_history:
            # Archivage : le produit reste en base pour la traçabilité
            product.is_active = False
            product.save(update_fields=['is_active'])
            django_messages.success(request, f'Produit "{product.name}" archivé. Il n\'est plus visible en boutique mais son historique est conservé.')
        else:
            name = product.name
            product.delete()
            django_messages.success(request, f'Produit "{name}" supprimé (aucun historique).')
    return redirect('dashboard:products')


@login_required
@seller_or_admin_required
def dash_product_unarchive(request, pk):
    """Réactive un produit archivé."""
    product = get_object_or_404(Product, pk=pk)
    is_admin = request.user.is_superuser or request.user.role == 'admin'
    if not is_admin and product.store != request.user.store:
        django_messages.error(request, 'Vous ne pouvez pas modifier ce produit.')
        return redirect('dashboard:products')
    if request.method == 'POST':
        product.is_active = True
        product.save(update_fields=['is_active'])
        django_messages.success(request, f'Produit "{product.name}" réactivé.')
    return redirect('dashboard:products')


SALES_PERIODS = [('today', "Aujourd'hui"), ('7d', '7 jours'), ('30d', '30 jours'), ('month', 'Ce mois'), ('all', 'Tout')]


def _pdf_brand(p, x, baseline, size_mm=8):
    """Dessine le symbole Comptoir à gauche d'un titre de PDF ; retourne l'abscisse du texte."""
    from reportlab.lib.units import mm
    from django.contrib.staticfiles import finders
    path = finders.find('images/brand/mark-96.png')
    if path:
        p.drawImage(path, x, baseline - 1.6 * mm, width=size_mm * mm, height=size_mm * mm, mask='auto')
        return x + (size_mm + 3) * mm
    return x


def _sales_queryset(request):
    """Lignes de vente payées visibles + filtres GET (page Ventes et son export).
    Retourne None si l'accès est refusé."""
    from datetime import datetime
    allowed, store, limit = access.page_scope(request.user, 'sales.view')
    if not allowed:
        return None
    items = OrderItem.objects.filter(order__is_paid=True)
    if store is not None:
        items = items.filter(store=store)
    warehouse, invalid = access.resolve_warehouse(request, store, limit)
    if invalid:
        items = items.none()
    elif warehouse:
        items = items.filter(warehouse=warehouse)

    q = request.GET.get('q', '').strip()
    if q:
        items = items.filter(Q(product__name__icontains=q) | Q(order__order_number__icontains=q)
                             | Q(order__shipping_name__icontains=q))

    # Période (dates locales, heure de Douala)
    today = timezone.localdate()
    period = request.GET.get('period', 'all')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    start = end = None
    if date_from or date_to:
        period = 'custom'
        try:
            start = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else None
            end = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else None
        except ValueError:
            start = end = None
    elif period == 'today':
        start = end = today
    elif period == '7d':
        start, end = today - timedelta(days=6), today
    elif period == '30d':
        start, end = today - timedelta(days=29), today
    elif period == 'month':
        start, end = today.replace(day=1), today
    else:
        period = 'all'
    if start:
        items = items.filter(order__created_at__date__gte=start)
    if end:
        items = items.filter(order__created_at__date__lte=end)
    return {
        'items': items, 'store': store, 'limit': limit, 'warehouse': warehouse,
        'search_query': q, 'period': period, 'date_from': date_from, 'date_to': date_to,
        'start': start, 'end': end, 'today': today,
    }


@login_required
@seller_or_admin_required
def dash_sales(request):
    """Page Ventes : lignes de vente payées, statistiques, graphique et top produits"""
    from django.core.paginator import Paginator
    from inventory.models import ProductStock
    data = _sales_queryset(request)
    if data is None:
        django_messages.error(request, "Vous n'avez pas accès aux ventes.")
        return redirect('dashboard:index')
    items, store, warehouse, today = data['items'], data['store'], data['warehouse'], data['today']
    line_total = F('price') * F('quantity')

    total_revenue = items.aggregate(t=Sum(line_total))['t'] or 0
    total_qty = items.aggregate(t=Sum('quantity'))['t'] or 0
    orders_count = items.values('order').distinct().count()
    avg_basket = int(total_revenue / orders_count) if orders_count else 0
    month_revenue = (items.filter(order__created_at__date__gte=today.replace(day=1))
                     .aggregate(t=Sum(line_total))['t'] or 0)

    # Graphique : CA par jour sur la période (30 derniers jours si "Tout"), 31 jours max
    chart_end = data['end'] or today
    chart_start = data['start'] or (chart_end - timedelta(days=29))
    if (chart_end - chart_start).days > 30:
        chart_start = chart_end - timedelta(days=30)
    per_day = dict(
        items.filter(order__created_at__date__gte=chart_start, order__created_at__date__lte=chart_end)
        .annotate(day=TruncDay('order__created_at')).values('day')
        .annotate(t=Sum(line_total)).values_list('day', 't')
    )
    per_day = {(d.date() if hasattr(d, 'date') else d): int(v or 0) for d, v in per_day.items()}
    chart, day = [], chart_start
    while day <= chart_end:
        chart.append({'day': day, 'value': per_day.get(day, 0)})
        day += timedelta(days=1)
    chart_max = max([c['value'] for c in chart] + [1])
    for c in chart:
        c['pct'] = round(c['value'] * 100 / chart_max) if c['value'] else 0

    top_products = (items.values('product__name')
                    .annotate(qty=Sum('quantity'), revenue=Sum(line_total))
                    .order_by('-revenue')[:5])

    page_obj = Paginator(items.select_related('order', 'order__buyer', 'product', 'warehouse')
                         .order_by('-order__created_at', '-pk'), 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)

    # Vente directe : produits avec le stock de l'entrepôt affiché (ou global)
    sale_store = store or (warehouse.store if warehouse else None)
    can_sell = bool(sale_store) and access.stock_access(request.user, sale_store, 'sales.create', warehouse)
    my_products = []
    if can_sell:
        my_products = list(sale_store.products.filter(is_active=True).order_by('name'))
        if warehouse:
            here = dict(ProductStock.objects.filter(warehouse=warehouse).values_list('product_id', 'quantity'))
            for prod in my_products:
                prod.sell_stock = here.get(prod.pk, 0)
        else:
            for prod in my_products:
                prod.sell_stock = prod.stock
        my_products = [prod for prod in my_products if prod.sell_stock > 0]

    return render(request, 'dashboard/sales.html', {
        'items': page_obj,
        'page_obj': page_obj,
        'total_revenue': total_revenue,
        'total_qty': total_qty,
        'month_revenue': month_revenue,
        'orders_count': orders_count,
        'avg_basket': avg_basket,
        'search_query': data['search_query'],
        'period': data['period'],
        'periods': SALES_PERIODS,
        'date_from': data['date_from'],
        'date_to': data['date_to'],
        'chart': chart,
        'top_products': top_products,
        'now': timezone.localtime(),
        'my_products': my_products,
        'can_sell': can_sell,
        'warehouse': warehouse,
        'warehouses': access.selectable_warehouses(store, data['limit']),
        'wh_query': f'warehouse={warehouse.pk}&' if warehouse else '',
        'base_query': params.urlencode(),
        'payment_choices': Order.PAYMENT_CHOICES,
    })


@login_required
@seller_or_admin_required
def dash_sales_export(request):
    """Export Excel des ventes (mêmes filtres que la page)"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    data = _sales_queryset(request)
    if data is None:
        django_messages.error(request, "Vous n'avez pas accès aux ventes.")
        return redirect('dashboard:index')
    wb = Workbook()
    ws = wb.active
    ws.title = 'Ventes'
    ws.append(['Date', 'Commande', 'Canal', 'Client', 'Produit', 'Entrepôt', 'Qté', 'Prix unit. (F)', 'Total (F)'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    rows = (data['items'].select_related('order', 'order__buyer', 'product', 'warehouse')
            .order_by('-order__created_at', '-pk')[:10000])
    for it in rows:
        o = it.order
        ws.append([
            timezone.localtime(o.created_at).strftime('%d/%m/%Y %H:%M'), o.order_number,
            'Vente directe' if o.is_direct_sale else 'En ligne',
            o.shipping_name or o.buyer.display_name, it.product.name,
            it.warehouse.code if it.warehouse else '', it.quantity, int(it.price), int(it.price * it.quantity),
        ])
    for col, width in zip('ABCDEFGHI', (17, 16, 14, 24, 34, 12, 6, 14, 14)):
        ws.column_dimensions[col].width = width
    wh = data['warehouse']
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="ventes{"_" + wh.code if wh else ""}_{timezone.localdate():%Y%m%d}.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_sale_create(request):
    """Vente directe : vendre sans commande en ligne (comptoir, téléphone...).
    Le stock sort de l'entrepôt choisi (celui de la page), sinon de l'entrepôt par défaut."""
    from django.db import transaction
    from inventory.models import Warehouse, ProductStock
    if request.method != 'POST':
        return redirect('dashboard:sales')
    store = _acting_store(request.user)
    if not store:
        django_messages.error(request, "Vous devez avoir une boutique.")
        return redirect('dashboard:sales')

    limit = _member_warehouse_id(request.user, store)
    warehouse_id = limit or request.POST.get('warehouse') or None
    if warehouse_id:
        warehouse = Warehouse.objects.filter(pk=warehouse_id, store=store).first()
        if warehouse is None:
            django_messages.error(request, "Entrepôt invalide.")
            return _safe_back(request, 'dashboard:sales')
    else:
        warehouse = Warehouse.objects.filter(store=store, is_default=True).first()
    if not _stock_access(request.user, store, 'sales.create', warehouse):
        django_messages.error(request, "Vous n'avez pas le droit d'enregistrer une vente.")
        return _safe_back(request, 'dashboard:sales')

    customer_name = request.POST.get('customer_name', '').strip() or 'Client comptoir'
    customer_phone = request.POST.get('customer_phone', '').strip()
    payment_method = request.POST.get('payment_method', 'cash')
    if payment_method not in dict(Order.PAYMENT_CHOICES):
        payment_method = 'cash'

    # Regrouper les lignes d'un même produit, puis vérifier le stock disponible
    wanted = {}
    for pid, qty in _parse_items(request):
        wanted[pid] = wanted.get(pid, 0) + qty
    products = {p.pk: p for p in store.products.filter(pk__in=wanted)}
    here = {}
    if warehouse:
        here = dict(ProductStock.objects.filter(warehouse=warehouse, product__in=products.values())
                    .values_list('product_id', 'quantity'))
    lines = []
    for pid, qty in wanted.items():
        product = products.get(pid)
        if not product:
            continue
        available = min(product.stock, here.get(pid, 0)) if warehouse else product.stock
        if qty > available:
            where = f' dans {warehouse.name}' if warehouse else ''
            django_messages.error(request, f'Stock insuffisant pour "{product.name}"{where} : {available} disponible(s).')
            return _safe_back(request, 'dashboard:sales')
        lines.append((product, qty))
    if not lines:
        django_messages.error(request, 'Ajoutez au moins un produit.')
        return _safe_back(request, 'dashboard:sales')

    total = sum(p.price * q for p, q in lines)
    with transaction.atomic():
        order = Order.objects.create(
            buyer=request.user,
            status='delivered',
            is_paid=True,
            payment_method=payment_method,
            subtotal=total,
            shipping_cost=0,
            total_amount=total,
            shipping_name=customer_name,
            shipping_phone=customer_phone or '—',
            shipping_address=Order.DIRECT_SALE_ADDRESS,
            shipping_city=store.city,
            notes=f'Vente directe — {customer_name}',
        )
        for product, qty in lines:
            OrderItem.objects.create(order=order, product=product, store=store, warehouse=warehouse, quantity=qty, price=product.price)
            product.orders_count += qty
            product.save(update_fields=['orders_count'])
            product.adjust_stock(-qty, 'sale', user=request.user, reason='Vente directe', reference=order.order_number, warehouse=warehouse)

    django_messages.success(request, f'Vente {order.order_number} enregistrée : {len(lines)} produit(s), {total:,.0f} F.')
    return _safe_back(request, 'dashboard:sales')


# ======== DEVIS RFQ (dashboard vendeur) ========
@login_required
@seller_or_admin_required
def dash_rfqs(request):
    """Liste des demandes de devis ouvertes + création de demande — dans le dashboard"""
    from orders.models import RFQ

    # Le vendeur peut aussi créer une demande de devis (il achète aussi)
    if request.method == 'POST':
        RFQ.objects.create(
            buyer=request.user,
            product_name=request.POST.get('product_name'),
            category_id=request.POST.get('category') or None,
            description=request.POST.get('description'),
            quantity=int(request.POST.get('quantity', 1)),
            target_price=request.POST.get('target_price') or None,
            unit=request.POST.get('unit', 'pièce'),
        )
        django_messages.success(request, 'Votre demande de devis a été publiée !')
        return redirect('dashboard:rfqs')

    from orders.models import Quote
    view = request.GET.get('view', 'open')

    # RFQ où le vendeur a déjà soumis un devis
    my_quoted_ids = []
    if (request.user.store is not None):
        my_quoted_ids = list(Quote.objects.filter(seller=request.user).values_list('rfq_id', flat=True))

    # Mes propres demandes de devis
    my_rfqs = RFQ.objects.filter(buyer=request.user).annotate(quote_cnt=Count('quotes')).order_by('-created_at')

    # Liste principale selon l'onglet
    if view == 'mine':
        rfqs = my_rfqs
    elif view == 'quoted':
        rfqs = RFQ.objects.filter(pk__in=my_quoted_ids).annotate(quote_cnt=Count('quotes')).order_by('-created_at')
    else:
        rfqs = RFQ.objects.filter(status__in=('open', 'quoted')).exclude(buyer=request.user).annotate(quote_cnt=Count('quotes')).order_by('-created_at')

    q = request.GET.get('q', '').strip()
    if q:
        rfqs = rfqs.filter(Q(product_name__icontains=q) | Q(description__icontains=q))
    category = request.GET.get('category')
    if category:
        rfqs = rfqs.filter(category_id=category)

    return render(request, 'dashboard/rfqs.html', {
        'rfqs': rfqs,
        'my_rfqs': my_rfqs,
        'categories': Category.objects.filter(parent__isnull=True),
        'search_query': q,
        'category_filter': category,
        'current_view': view,
        'my_quoted_ids': my_quoted_ids,
        'total_open': RFQ.objects.filter(status__in=('open', 'quoted')).exclude(buyer=request.user).count(),
        'my_quotes_count': len(my_quoted_ids),
        'my_rfqs_count': my_rfqs.count(),
        'quotes_received_count': Quote.objects.filter(rfq__buyer=request.user).count(),
    })


@login_required
@seller_or_admin_required
def dash_rfq_detail(request, rfq_id):
    """Détail RFQ + soumission de devis — dans le dashboard"""
    from orders.models import RFQ, Quote
    rfq = get_object_or_404(RFQ, pk=rfq_id)
    user_quote = Quote.objects.filter(rfq=rfq, seller=request.user).first()

    if request.method == 'POST':
        from orders.rfq_services import QuoteError, submit_quote
        try:
            _, created = submit_quote(rfq, request.user, request.POST)
            django_messages.success(request, "Votre offre a été envoyée à l'acheteur." if created else 'Votre offre a été mise à jour.')
        except QuoteError as exc:
            django_messages.error(request, str(exc))
        return redirect('dashboard:rfq_detail', rfq_id=rfq_id)

    return render(request, 'dashboard/rfq_detail.html', {
        'rfq': rfq,
        'user_quote': user_quote,
    })


# ======== ENTREPÔTS ========
# Règles d'accès partagées avec la facturation : voir store/access.py
_has_perm = access.has_perm
_stock_access = access.stock_access
_acting_store = access.acting_store
_member_warehouse_id = access.member_warehouse_id


def _get_warehouse_or_deny(request, pk, permission):
    """Retourne (entrepôt, None) si l'accès est autorisé, sinon (None, redirection)."""
    from inventory.models import Warehouse
    warehouse = get_object_or_404(Warehouse.objects.select_related('store', 'manager'), pk=pk)
    if not _stock_access(request.user, warehouse.store, permission, warehouse):
        django_messages.error(request, "Accès refusé.")
        return None, redirect('dashboard:warehouses')
    return warehouse, None


def _safe_back(request, fallback):
    """Redirige vers la page précédente si elle est sur ce site, sinon vers fallback."""
    from django.utils.http import url_has_allowed_host_and_scheme
    back = request.META.get('HTTP_REFERER', '')
    if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect(*fallback) if isinstance(fallback, tuple) else redirect(fallback)


def _parse_items(request):
    """Lit les lignes product[] / quantity[] d'un formulaire. Ignore les lignes vides ou invalides."""
    items = []
    quantities = request.POST.getlist('quantity[]')
    for i, pid in enumerate(request.POST.getlist('product[]')):
        try:
            qty = int(quantities[i]) if i < len(quantities) else 0
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            items.append((pid, qty))
    return items


@login_required
@seller_or_admin_required
def dash_warehouses(request):
    """Liste des entrepôts + création"""
    from inventory.models import Warehouse
    store = getattr(request.user, 'store', None)
    is_admin = request.user.is_superuser or request.user.role == 'admin'

    if request.method == 'POST':
        if not store:
            django_messages.error(request, "Vous devez avoir une boutique.")
            return redirect('dashboard:warehouses')
        wh = Warehouse.objects.create(
            store=store,
            name=request.POST.get('name'),
            code=request.POST.get('code', '').upper(),
            address=request.POST.get('address', ''),
            city=request.POST.get('city', store.city),
        )
        # Lier à des boutiques supplémentaires du même propriétaire
        linked = request.POST.getlist('linked_stores')
        if linked:
            wh.linked_stores.set(Store.objects.filter(pk__in=linked, owner=request.user))
        django_messages.success(request, 'Entrepôt créé !')
        return redirect('dashboard:warehouses')

    if is_admin:
        warehouses = Warehouse.objects.all()
    elif store:
        warehouses = store.warehouses.all()
    else:
        # Employé : entrepôts de sa boutique, ou seulement le sien s'il y est limité
        member_store = _acting_store(request.user)
        warehouses = member_store.warehouses.all() if member_store else Warehouse.objects.none()
        limit = _member_warehouse_id(request.user, member_store) if member_store else None
        if limit:
            warehouses = warehouses.filter(pk=limit)
    other_stores = Store.objects.filter(owner=request.user).exclude(pk=store.pk) if store else []
    # Employé rattaché à un seul entrepôt : directement sur sa page
    if not store and not is_admin and warehouses.count() == 1:
        return redirect('dashboard:warehouse_detail', pk=warehouses.first().pk)
    # À traiter dans chaque entrepôt : commandes en attente, caisses ouvertes
    from django.db.models import Count, Q as _Q
    warehouses = warehouses.annotate(
        pending_orders=Count('orderitem__order', filter=_Q(orderitem__order__status='pending'), distinct=True),
        open_registers=Count('cash_registers__sessions', filter=_Q(cash_registers__sessions__status='open'), distinct=True),
    )
    acting = store or _acting_store(request.user)
    show_stock = is_admin or (acting is not None and access.has_perm(request.user, acting, 'stock.view'))
    return render(request, 'dashboard/warehouses.html', {
        'show_stock': show_stock,
        'warehouses': warehouses,
        'other_stores': other_stores,
        'can_create': bool(store),
    })


@login_required
@seller_or_admin_required
def dash_warehouse_detail(request, pk):
    """Page d'un entrepôt : point d'entrée unique vers ses caisses, commandes, ventes, factures, produits et stock.
    Ouverte à quiconque a au moins un de ces droits sur cet entrepôt ; chaque bloc suit son propre droit."""
    from inventory.models import Warehouse
    warehouse = get_object_or_404(Warehouse.objects.select_related('store', 'manager'), pk=pk)
    from pos import services as pos_services
    pos_scope = pos_services.scope_for(request.user)
    pos_ok = bool(pos_scope and pos_scope.can_use and pos_scope.store == warehouse.store and pos_scope.warehouse_ok(warehouse.pk))
    perm = lambda p: _stock_access(request.user, warehouse.store, p, warehouse)  # noqa: E731
    can = {
        'stock': perm('stock.view'),
        'transfer': perm('stock.transfer'),
        'reception': perm('stock.adjust'),
        'orders': perm('orders.view'),
        'products': perm('products.view'),
        'sales': perm('sales.view'),
        'invoicing': perm('invoicing.view'),
        'finances': perm('finances.view'),
        'owner': perm(None),
        'pos': pos_ok,
        'pos_manage': pos_ok and pos_scope.can_manage,
    }
    if not any(can.values()):
        django_messages.error(request, "Accès refusé.")
        return redirect('dashboard:warehouses')

    stocks = warehouse.stocks.select_related('product').order_by('product__name') if can['stock'] else warehouse.stocks.none()
    q = request.GET.get('q', '').strip()
    if q:
        stocks = stocks.filter(product__name__icontains=q)

    # Alertes pour les badges sur les cartes
    from orders.models import OrderItem
    from invoicing.models import Invoice
    from store.models import StoreMember
    pending_orders = OrderItem.objects.filter(
        warehouse=warehouse, order__status='pending'
    ).values('order').distinct().count()
    low_stock_count = warehouse.stocks.filter(quantity__lte=F('product__low_stock_threshold'), quantity__gt=0).count()
    out_of_stock_count = warehouse.stocks.filter(quantity=0).count()
    unpaid_invoices = Invoice.objects.filter(warehouse=warehouse, status='sent').count()
    employees_count = StoreMember.objects.filter(warehouse=warehouse, is_active=True).count()

    # Caisses de cet entrepôt : état de chacune, et ce que l'utilisateur peut y faire
    pos_info, registers, my_session = None, [], None
    if pos_ok:
        from pos.models import POSSession
        from django.db.models import Sum as _Sum
        if not warehouse.cash_registers.exists() and pos_scope.can_manage:
            pos_services.ensure_default_register(warehouse)
        registers = list(warehouse.cash_registers.filter(is_active=True))
        today = timezone.localdate()
        open_by_reg = {s.register_id: s for s in POSSession.objects.filter(register__in=registers, status='open').select_related('cashier')}
        totals = dict(warehouse.pos_sales.filter(completed_at__date=today, status__in=('completed', 'refunded'))
                      .values_list('session__register').annotate(t=_Sum('total_amount')))
        for reg in registers:
            reg.current = open_by_reg.get(reg.pk)
            reg.today_total = totals.get(reg.pk) or 0
            reg.mine = bool(reg.current and reg.current.cashier_id == request.user.pk)
        my_session = pos_services.current_session(request.user, warehouse.store)
        pos_info = {'count': len(registers), 'open': len(open_by_reg),
                    'today': pos_services.figures(warehouse.pos_sales.filter(completed_at__date=today))['net']}

    return render(request, 'dashboard/warehouse_detail.html', {
        'pos_info': pos_info, 'registers': registers, 'my_session': my_session,
        # Une seule caisse libre : le bouton l'ouvre directement
        'free_register': (lambda f: f[0] if len(f) == 1 else None)([r for r in registers if not r.current]),
        'warehouse': warehouse,
        'stocks': stocks,
        'search_query': q,
        'products': warehouse.store.products.filter(is_active=True),
        'other_warehouses': warehouse.store.warehouses.filter(is_active=True).exclude(pk=warehouse.pk),
        'pending_orders': pending_orders,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'unpaid_invoices': unpaid_invoices,
        'employees_count': employees_count,
        'can': can,
    })


@login_required
@seller_or_admin_required
def dash_warehouse_reception(request, pk):
    """Réception de marchandises dans un entrepôt (entrée de stock)"""
    from django.db import transaction
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'stock.adjust')
    if denied:
        return denied

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip() or 'Réception marchandises'
        items = _parse_items(request)
        products = {p.pk: p for p in Product.objects.filter(pk__in=[pid for pid, _ in items], store=warehouse.store)}
        count = 0
        with transaction.atomic():
            for pid, qty in items:
                product = products.get(pid)
                if product:
                    product.adjust_stock(qty, 'in', user=request.user, reason=reason,
                                         reference=warehouse.code, warehouse=warehouse)
                    count += 1
        if count:
            django_messages.success(request, f'Réception enregistrée : {count} produit(s) ajouté(s) à {warehouse.name}.')
        else:
            django_messages.error(request, 'Aucun produit valide.')
    return _safe_back(request, ('dashboard:warehouse_detail', pk))


@login_required
@seller_or_admin_required
def dash_warehouse_employees(request, pk):
    """Employés assignés à un entrepôt précis (réservé au propriétaire)"""
    warehouse, denied = _get_warehouse_or_deny(request, pk, None)
    if denied:
        return denied

    members = warehouse.store.members.filter(warehouse=warehouse).select_related('user', 'role')
    return render(request, 'dashboard/warehouse_employees.html', {
        'warehouse': warehouse,
        'members': members,
    })


def _warehouse_orders_queryset(request, warehouse):
    """Commandes ayant au moins une ligne dans cet entrepôt, avec le montant propre à l'entrepôt.
    Retourne (commandes filtrées, base sans filtre de statut, contexte des filtres)."""
    from datetime import datetime
    from django.db.models import DecimalField, OuterRef, Prefetch, Subquery
    wh_items = OrderItem.objects.filter(warehouse=warehouse)
    # Une commande marketplace peut contenir d'autres vendeurs : on ne somme que les lignes de l'entrepôt
    wh_total = (OrderItem.objects.filter(order=OuterRef('pk'), warehouse=warehouse)
                .values('order').annotate(t=Sum(F('price') * F('quantity'))).values('t')[:1])
    orders = (Order.objects.filter(pk__in=wh_items.values('order'))
              .select_related('buyer')
              .annotate(wh_total=Subquery(wh_total, output_field=DecimalField(max_digits=18, decimal_places=0)))
              .prefetch_related(Prefetch('items', queryset=wh_items.select_related('product'), to_attr='wh_items')))
    q = request.GET.get('q', '').strip()
    if q:
        orders = orders.filter(Q(order_number__icontains=q) | Q(buyer__username__icontains=q)
                               | Q(shipping_name__icontains=q) | Q(shipping_phone__icontains=q))
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    for value, lookup in ((date_from, 'created_at__date__gte'), (date_to, 'created_at__date__lte')):
        if value:
            try:
                orders = orders.filter(**{lookup: datetime.strptime(value, '%Y-%m-%d').date()})
            except ValueError:
                pass
    base = orders
    status = request.GET.get('status', '')
    if status in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status)
    else:
        status = ''
    return orders.order_by('-created_at'), base, {
        'search_query': q, 'current_status': status, 'date_from': date_from, 'date_to': date_to,
    }


@login_required
@seller_or_admin_required
def dash_warehouse_orders(request, pk):
    """Commandes contenant des produits d'un entrepôt"""
    from django.core.paginator import Paginator
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'orders.view')
    if denied:
        return denied
    orders, base, filters = _warehouse_orders_queryset(request, warehouse)
    counts = dict(base.order_by().values_list('status').annotate(n=Count('id')))
    active = base.exclude(status__in=['cancelled', 'refunded'])
    stats = {
        'total': sum(counts.values()),
        'to_prepare': counts.get('pending', 0) + counts.get('confirmed', 0) + counts.get('processing', 0),
        'shipped': counts.get('shipped', 0),
        'delivered': counts.get('delivered', 0),
        'amount': active.aggregate(t=Sum('wh_total'))['t'] or 0,
    }
    page_obj = Paginator(orders, 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'dashboard/warehouse_orders.html', {
        'warehouse': warehouse,
        'orders': page_obj,
        'page_obj': page_obj,
        'stats': stats,
        'status_chips': [(code, label, counts.get(code, 0)) for code, label in Order.STATUS_CHOICES],
        'base_query': params.urlencode(),
        'can_invoice': _has_perm(request.user, warehouse.store, 'invoicing.create'),
        **filters,
    })


@login_required
@seller_or_admin_required
def dash_warehouse_orders_export(request, pk):
    """Export Excel des commandes d'un entrepôt (mêmes filtres que la page)"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'orders.view')
    if denied:
        return denied
    orders = _warehouse_orders_queryset(request, warehouse)[0]
    wb = Workbook()
    ws = wb.active
    ws.title = 'Commandes'
    ws.append(['N°', 'Date', 'Client', 'Téléphone', 'Ville', 'Produits (cet entrepôt)', 'Montant entrepôt (F)', 'Total commande (F)', 'Statut', 'Payée'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for o in orders[:5000]:
        ws.append([
            o.order_number, timezone.localtime(o.created_at).strftime('%d/%m/%Y %H:%M'),
            o.shipping_name or o.buyer.display_name, o.shipping_phone, o.shipping_city,
            ', '.join(f'{i.product.name} ×{i.quantity}' for i in o.wh_items),
            int(o.wh_total or 0), int(o.total_amount), o.get_status_display(), 'Oui' if o.is_paid else 'Non',
        ])
    for col, width in zip('ABCDEFGHIJ', (16, 17, 24, 15, 14, 44, 18, 16, 14, 7)):
        ws.column_dimensions[col].width = width
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="commandes_{warehouse.code}_{timezone.localdate():%Y%m%d}.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_warehouse_stores(request, pk):
    """Boutiques alimentées par un entrepôt"""
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'stock.view')
    if denied:
        return denied
    return render(request, 'dashboard/warehouse_stores.html', {'warehouse': warehouse})


def _warehouse_stock_queryset(request, warehouse):
    """Stocks d'un entrepôt filtrés et triés selon les paramètres GET."""
    from django.db.models import DecimalField, ExpressionWrapper
    stocks = warehouse.stocks.select_related('product').annotate(
        value=ExpressionWrapper(F('quantity') * F('product__price'), output_field=DecimalField(max_digits=18, decimal_places=0)),
    )
    q = request.GET.get('q', '').strip()
    if q:
        stocks = stocks.filter(Q(product__name__icontains=q) | Q(product__sku__icontains=q))
    status = request.GET.get('status', '')
    if status == 'out':
        stocks = stocks.filter(quantity=0)
    elif status == 'low':
        stocks = stocks.filter(quantity__gt=0, quantity__lte=F('product__low_stock_threshold'))
    elif status == 'ok':
        stocks = stocks.filter(quantity__gt=F('product__low_stock_threshold'))
    sort = request.GET.get('sort', 'name')
    order = {'qty': 'quantity', '-qty': '-quantity', '-value': '-value'}.get(sort, 'product__name')
    return stocks.order_by(order, 'product__name'), q, status, sort


@login_required
@seller_or_admin_required
def dash_warehouse_stock(request, pk):
    """Stock détaillé d'un entrepôt : stats, filtres, tri, actions d'entrée/sortie"""
    from django.core.paginator import Paginator
    from django.db.models import DecimalField
    from django.db.models.functions import Coalesce
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'stock.view')
    if denied:
        return denied

    stats = warehouse.stocks.aggregate(
        products=Count('id'),
        units=Coalesce(Sum('quantity'), 0),
        value=Coalesce(Sum(F('quantity') * F('product__price'), output_field=DecimalField()), 0, output_field=DecimalField()),
        low=Count('id', filter=Q(quantity__gt=0, quantity__lte=F('product__low_stock_threshold'))),
        out=Count('id', filter=Q(quantity=0)),
    )
    stocks, q, status, sort = _warehouse_stock_queryset(request, warehouse)
    page_obj = Paginator(stocks, 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)

    return render(request, 'dashboard/warehouse_stock.html', {
        'warehouse': warehouse,
        'stocks': page_obj,
        'page_obj': page_obj,
        'stats': stats,
        'search_query': q,
        'status_filter': status,
        'sort': sort,
        'base_query': params.urlencode(),
        'can_adjust': _stock_access(request.user, warehouse.store, 'stock.adjust', warehouse),
        'can_transfer': _stock_access(request.user, warehouse.store, 'stock.transfer', warehouse),
        'products': warehouse.store.products.filter(is_active=True).order_by('name'),
    })


@login_required
@seller_or_admin_required
def dash_warehouse_stock_export(request, pk):
    """Export Excel du stock d'un entrepôt (mêmes filtres que la page)"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'stock.view')
    if denied:
        return denied

    stocks = _warehouse_stock_queryset(request, warehouse)[0]
    wb = Workbook()
    ws = wb.active
    ws.title = 'Stock'
    ws.append(['Produit', 'Quantité', "Seuil d'alerte", 'Prix unitaire (F)', 'Valeur (F)', 'Statut'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for s in stocks:
        threshold = s.product.low_stock_threshold
        status = 'Rupture' if s.quantity == 0 else ('Stock faible' if s.quantity <= threshold else 'En stock')
        ws.append([s.product.name, s.quantity, threshold, int(s.product.price), int(s.value or 0), status])
    for col, width in zip('ABCDEF', (40, 12, 14, 18, 16, 14)):
        ws.column_dimensions[col].width = width

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="stock_{warehouse.code}_{timezone.now():%Y%m%d}.xlsx"'
    wb.save(response)
    return response


MONTHS_FR = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin', 'Juillet',
             'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']
RECEIPT_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png', '.webp')
RECEIPT_MAX_SIZE = 5 * 1024 * 1024


def _shift_month(year, month, delta):
    """(année, mois) décalé de `delta` mois."""
    y, m = divmod(year * 12 + month - 1 + delta, 12)
    return y, m + 1


def _warehouse_month_figures(warehouse, year, month):
    """Chiffres d'un mois pour un entrepôt.

    - revenus : lignes de commandes payées de l'entrepôt (hors annulées ou remboursées)
      + ventes de caisse POS terminées dans cet entrepôt (hors TVA collectée) ;
    - commission : prélevée uniquement sur les ventes en ligne (ventes directes et POS n'en paient pas,
      comme dans le crédit du portefeuille vendeur) ;
    - coût des marchandises vendues : quantité × prix d'achat figé à la vente (à défaut, prix d'achat actuel
      du produit) ; les lignes sans aucun prix d'achat sont comptées à 0 et signalées dans `missing_cost` ;
    - dépenses : dépenses saisies pour l'entrepôt sur le mois.
    """
    from decimal import Decimal
    from django.conf import settings
    from django.db.models import DecimalField, ExpressionWrapper
    from django.db.models.functions import Coalesce
    from pos.models import POSSale, POSSaleItem
    money = DecimalField(max_digits=18, decimal_places=2)

    def cost_of(prefix=''):
        unit = Coalesce(f'{prefix}unit_cost', f'{prefix}product__cost_price', output_field=money)
        return ExpressionWrapper(F(f'{prefix}quantity') * unit, output_field=money)

    no_cost = Q(unit_cost__isnull=True) & (Q(product__isnull=True) | Q(product__cost_price__isnull=True))

    items = OrderItem.objects.filter(
        warehouse=warehouse, order__is_paid=True,
        order__created_at__year=year, order__created_at__month=month,
    ).exclude(order__status__in=['cancelled', 'refunded'])
    line = F('price') * F('quantity')
    direct = Q(order__shipping_address=Order.DIRECT_SALE_ADDRESS)
    sums = items.aggregate(online=Sum(line, filter=~direct), direct=Sum(line, filter=direct),
                           cogs=Sum(cost_of()), missing=Count('id', filter=no_cost))

    pos_sales = POSSale.objects.filter(warehouse=warehouse, status='completed',
                                       created_at__year=year, created_at__month=month)
    from pos.services import NET_REVENUE, item_cost
    pos_sums = pos_sales.aggregate(net=Sum(NET_REVENUE), count=Count('id'))
    pos_items = POSSaleItem.objects.filter(sale__in=pos_sales).aggregate(cogs=Sum(item_cost()), missing=Count('id', filter=no_cost))

    online, direct_rev = int(sums['online'] or 0), int(sums['direct'] or 0)
    pos_rev = int(pos_sums['net'] or 0)
    commission = int(Decimal(online) * Decimal(str(settings.SALES_COMMISSION_RATE)))
    cogs = int((sums['cogs'] or 0) + (pos_items['cogs'] or 0))
    expenses = int(warehouse.expenses.filter(date__year=year, date__month=month).aggregate(t=Sum('amount'))['t'] or 0)
    revenue = online + direct_rev + pos_rev
    return {
        'revenue': revenue, 'online': online, 'direct': direct_rev, 'pos': pos_rev, 'pos_count': pos_sums['count'],
        'commission': commission, 'cogs': cogs, 'gross': revenue - cogs,
        'missing_cost': (sums['missing'] or 0) + (pos_items['missing'] or 0),
        'expenses': expenses, 'net': revenue - commission - cogs - expenses,
    }


def _variation(current, previous):
    """Variation en % par rapport au mois précédent (None si pas de base de comparaison)."""
    if not previous:
        return None
    return round((current - previous) * 100 / abs(previous))


@login_required
@seller_or_admin_required
def dash_warehouse_accounting(request, pk):
    """Comptabilité d'un entrepôt : revenus par canal, commissions, dépenses, résultat, évolution sur 6 mois"""
    from accounting.models import WarehouseExpense
    from datetime import datetime
    from django.conf import settings
    from store.models import Payslip

    warehouse, denied = _get_warehouse_or_deny(request, pk, 'finances.view')
    if denied:
        return denied
    can_add_expense = _stock_access(request.user, warehouse.store, None)

    # Ajouter une dépense (propriétaire uniquement), avec justificatif facultatif
    if request.method == 'POST':
        if not can_add_expense:
            django_messages.error(request, "Seul le propriétaire peut enregistrer une dépense.")
            return _safe_back(request, ('dashboard:warehouse_accounting', pk))
        description = request.POST.get('description', '').strip()[:300]
        categories = dict(WarehouseExpense.CATEGORY_CHOICES)
        category = request.POST.get('category', 'other')
        try:
            amount = int(request.POST.get('amount', 0))
        except (TypeError, ValueError):
            amount = 0
        try:
            date = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
        except ValueError:
            date = timezone.localdate()
        receipt = request.FILES.get('receipt')
        if not description or amount <= 0:
            django_messages.error(request, 'Indiquez une description et un montant supérieur à 0.')
        elif receipt and (not receipt.name.lower().endswith(RECEIPT_EXTENSIONS) or receipt.size > RECEIPT_MAX_SIZE):
            django_messages.error(request, 'Justificatif refusé : PDF ou image (JPG, PNG, WEBP) de 5 Mo maximum.')
        else:
            WarehouseExpense.objects.create(
                warehouse=warehouse,
                category=category if category in categories else 'other',
                description=description,
                amount=amount,
                date=date,
                receipt=receipt,
                created_by=request.user,
            )
            django_messages.success(request, 'Dépense enregistrée.')
        return _safe_back(request, ('dashboard:warehouse_accounting', pk))

    # Période (mois en cours, heure de Douala, par défaut) — valeurs invalides ignorées
    today = timezone.localdate()
    try:
        month = min(12, max(1, int(request.GET.get('month', today.month))))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year

    current = _warehouse_month_figures(warehouse, year, month)
    prev_year, prev_month = _shift_month(year, month, -1)
    previous = _warehouse_month_figures(warehouse, prev_year, prev_month)
    variations = {k: _variation(current[k], previous[k]) for k in ('revenue', 'gross', 'expenses', 'net')}

    # Dépenses du mois et répartition par catégorie
    expenses = warehouse.expenses.filter(date__year=year, date__month=month).select_related('created_by')
    labels = dict(WarehouseExpense.CATEGORY_CHOICES)
    by_category = []
    for row in expenses.order_by().values('category').annotate(t=Sum('amount')).order_by('-t'):
        total = int(row['t'] or 0)
        by_category.append({
            'label': labels.get(row['category'], row['category']), 'total': total,
            'pct': round(total * 100 / current['expenses']) if current['expenses'] else 0,
        })

    # Fiches de paie des employés rattachés à cet entrepôt (information : à saisir en dépense "Salaires")
    payslips = Payslip.objects.filter(member__warehouse=warehouse, year=year, month=month,
                                      status__in=['validated', 'paid'])
    payroll = int(payslips.aggregate(t=Sum('net_salary'))['t'] or 0)
    salary_expenses = int(expenses.filter(category='salary').aggregate(t=Sum('amount'))['t'] or 0)

    # Évolution sur les 6 mois qui se terminent au mois choisi
    monthly = []
    for i in range(5, -1, -1):
        y, m = _shift_month(year, month, -i)
        f = _warehouse_month_figures(warehouse, y, m)
        monthly.append({'label': f'{MONTHS_FR[m - 1][:3]}. {str(y)[2:]}', **f})

    # Années proposées : depuis la première donnée de l'entrepôt
    first_sale = OrderItem.objects.filter(warehouse=warehouse).order_by('order__created_at').values_list('order__created_at', flat=True).first()
    first_expense = warehouse.expenses.order_by('date').values_list('date', flat=True).first()
    first_year = min([d.year for d in (first_sale, first_expense) if d] + [warehouse.created_at.year, today.year, year])

    return render(request, 'dashboard/warehouse_accounting.html', {
        'warehouse': warehouse,
        'f': current,
        'previous': previous,
        'variations': variations,
        'revenue': current['revenue'],
        'commission': current['commission'],
        'total_expenses': current['expenses'],
        'net_profit': current['net'],
        'commission_pct': round(settings.SALES_COMMISSION_RATE * 100),
        'expenses': expenses,
        'by_category': by_category,
        'payroll': payroll,
        'payslips_count': payslips.count(),
        'salary_expenses': salary_expenses,
        'monthly': monthly,
        'current_month': month,
        'current_year': year,
        'month_label': f'{MONTHS_FR[month - 1]} {year}',
        'prev_label': MONTHS_FR[prev_month - 1],
        'prev_period': {'month': prev_month, 'year': prev_year},
        'next_period': dict(zip(('year', 'month'), _shift_month(year, month, 1))),
        'is_current_month': (year, month) == (today.year, today.month),
        'months': list(enumerate(MONTHS_FR, start=1)),
        'years': range(first_year, max(today.year, year) + 1),
        'expense_categories': WarehouseExpense.CATEGORY_CHOICES,
        'can_add_expense': can_add_expense,
        'today': today.isoformat(),
    })


@login_required
@seller_or_admin_required
def dash_warehouse_expense_delete(request, pk, expense_id):
    """Supprimer une dépense (propriétaire uniquement, formulaire POST)"""
    warehouse, denied = _get_warehouse_or_deny(request, pk, None)
    if denied:
        return denied
    expense = get_object_or_404(warehouse.expenses, pk=expense_id)
    if request.method == 'POST':
        label = expense.description
        if expense.receipt:
            expense.receipt.delete(save=False)
        expense.delete()
        django_messages.success(request, f'Dépense « {label} » supprimée.')
    return _safe_back(request, ('dashboard:warehouse_accounting', pk))


@login_required
@seller_or_admin_required
def dash_warehouse_accounting_export(request, pk):
    """Export Excel : synthèse du mois, évolution sur 6 mois et détail des dépenses"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    warehouse, denied = _get_warehouse_or_deny(request, pk, 'finances.view')
    if denied:
        return denied
    today = timezone.localdate()
    try:
        month = min(12, max(1, int(request.GET.get('month', today.month))))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    bold = Font(bold=True)
    f = _warehouse_month_figures(warehouse, year, month)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Synthèse'
    ws.append([f'{warehouse.name} ({warehouse.code}) — {MONTHS_FR[month - 1]} {year}'])
    ws['A1'].font = Font(bold=True, size=13)
    ws.append([])
    rows = (('Ventes en ligne', 'online'), ('Ventes directes', 'direct'), ('Ventes caisse (POS)', 'pos'),
            ('Revenus', 'revenue'), ('Coût des marchandises vendues', 'cogs'), ('Marge brute', 'gross'),
            ('Commissions plateforme', 'commission'), ('Dépenses', 'expenses'), ('Résultat net', 'net'))
    for label, key in rows:
        ws.append([label, f[key]])
        if key in ('revenue', 'gross', 'net'):
            ws.cell(row=ws.max_row, column=1).font = bold
            ws.cell(row=ws.max_row, column=2).font = bold
    if f['missing_cost']:
        ws.append([])
        ws.append([f"{f['missing_cost']} ligne(s) vendue(s) sans prix d'achat : coût compté à 0"])
    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 16

    ws2 = wb.create_sheet('6 mois')
    ws2.append(['Mois', 'Revenus', 'Coût marchandises', 'Commissions', 'Dépenses', 'Résultat'])
    for cell in ws2[1]:
        cell.font = bold
    for i in range(5, -1, -1):
        y, m = _shift_month(year, month, -i)
        fm = _warehouse_month_figures(warehouse, y, m)
        ws2.append([f'{MONTHS_FR[m - 1]} {y}', fm['revenue'], fm['cogs'], fm['commission'], fm['expenses'], fm['net']])
    ws2.column_dimensions['A'].width = 18

    ws3 = wb.create_sheet('Dépenses')
    ws3.append(['Date', 'Catégorie', 'Description', 'Montant (F)', 'Saisie par', 'Justificatif'])
    for cell in ws3[1]:
        cell.font = bold
    for e in warehouse.expenses.filter(date__year=year, date__month=month).select_related('created_by'):
        ws3.append([e.date.strftime('%d/%m/%Y'), e.get_category_display(), e.description, int(e.amount),
                    e.created_by.display_name if e.created_by else '', 'Oui' if e.receipt else 'Non'])
    for col, width in zip('ABCDEF', (12, 16, 40, 14, 20, 12)):
        ws3.column_dimensions[col].width = width

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="comptabilite_{warehouse.code}_{year}-{month:02d}.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_warehouse_expenses(request, pk):
    """Liste des dépenses d'un entrepôt avec filtres"""
    from accounting.models import WarehouseExpense
    from datetime import datetime

    warehouse, denied = _get_warehouse_or_deny(request, pk, 'finances.view')
    if denied:
        return denied

    expenses = warehouse.expenses.all()

    # Filtres
    category = request.GET.get('category', '')
    if category:
        expenses = expenses.filter(category=category)
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    if date_from:
        try:
            expenses = expenses.filter(date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            expenses = expenses.filter(date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    total = expenses.aggregate(t=Sum('amount'))['t'] or 0

    return render(request, 'dashboard/warehouse_expenses.html', {
        'warehouse': warehouse,
        'expenses': expenses,
        'total': total,
        'category_filter': category,
        'date_from': date_from,
        'date_to': date_to,
        'categories': WarehouseExpense.CATEGORY_CHOICES,
        'today': timezone.now().date().isoformat(),
        'can_add_expense': _stock_access(request.user, warehouse.store, None),
    })


# ======== TRANSFERTS ========
@login_required
@seller_or_admin_required
def dash_transfers(request):
    """Liste des transferts + création"""
    from inventory.models import StockTransfer, TransferItem, Warehouse
    from django.db import transaction
    store = _acting_store(request.user)

    if request.method == 'POST':
        if not store:
            django_messages.error(request, "Vous devez avoir une boutique.")
            return redirect('dashboard:transfers')
        from_wh = get_object_or_404(Warehouse, pk=request.POST.get('from_warehouse') or 0, store=store)
        to_wh = get_object_or_404(Warehouse, pk=request.POST.get('to_warehouse') or 0, store=store)
        if from_wh == to_wh:
            django_messages.error(request, "Les deux entrepôts doivent être différents.")
            return _safe_back(request, 'dashboard:transfers')
        # Un employé limité à un entrepôt ne peut transférer que depuis ou vers le sien
        if not (_stock_access(request.user, store, 'stock.transfer', from_wh)
                or _stock_access(request.user, store, 'stock.transfer', to_wh)):
            django_messages.error(request, "Vous n'avez pas le droit de créer ce transfert.")
            return _safe_back(request, 'dashboard:transfers')
        # Seuls les produits de la boutique sont acceptés
        items = _parse_items(request)
        valid_ids = set(store.products.filter(pk__in=[pid for pid, _ in items]).values_list('pk', flat=True))
        items = [(pid, qty) for pid, qty in items if pid in valid_ids]
        if not items:
            django_messages.error(request, "Ajoutez au moins un produit valide avec une quantité supérieure à 0.")
            return _safe_back(request, 'dashboard:transfers')
        ttype = request.POST.get('transfer_type', 'transfer')
        with transaction.atomic():
            transfer = StockTransfer.objects.create(
                store=store, from_warehouse=from_wh, to_warehouse=to_wh,
                transfer_type=ttype if ttype in dict(StockTransfer.TYPE_CHOICES) else 'transfer',
                notes=request.POST.get('notes', ''), created_by=request.user,
            )
            for pid, qty in items:
                TransferItem.objects.create(transfer=transfer, product_id=pid, quantity=qty)
        django_messages.success(request, f'Transfert {transfer.reference} créé en brouillon.')
        return redirect('dashboard:transfers')

    transfers = _scoped_transfers(request.user)
    status = request.GET.get('status', '')
    if status:
        transfers = transfers.filter(status=status)
    ttype = request.GET.get('ttype', '')
    if ttype:
        transfers = transfers.filter(transfer_type=ttype)

    # Recherche par référence
    q = request.GET.get('q', '').strip()
    if q:
        transfers = transfers.filter(Q(reference__icontains=q) | Q(from_warehouse__name__icontains=q) | Q(to_warehouse__name__icontains=q))

    # Filtres date
    from datetime import datetime
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    if date_from:
        try:
            transfers = transfers.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            transfers = transfers.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    return render(request, 'dashboard/transfers.html', {
        'transfers': transfers.select_related('from_warehouse', 'to_warehouse'),
        'warehouses': store.warehouses.filter(is_active=True) if store else [],
        'products': store.products.filter(is_active=True) if store else [],
        'can_transfer': bool(store) and _has_perm(request.user, store, 'stock.transfer'),
        'status_filter': status,
        'status_choices': StockTransfer.STATUS_CHOICES,
        'type_filter': ttype,
        'search_query': q,
        'date_from': date_from,
        'date_to': date_to,
    })


def _scoped_transfers(user):
    """Transferts visibles : tous (admin), ceux de la boutique, ou ceux de l'entrepôt d'un employé limité."""
    from inventory.models import StockTransfer
    if user.is_superuser or user.role == 'admin':
        return StockTransfer.objects.all()
    store = _acting_store(user)
    if not store or not _has_perm(user, store, 'stock.view'):
        return StockTransfer.objects.none()
    transfers = store.transfers.all()
    limit = _member_warehouse_id(user, store)
    if limit:
        transfers = transfers.filter(Q(from_warehouse_id=limit) | Q(to_warehouse_id=limit))
    return transfers


def _filtered_transfers(request):
    """Transferts filtrés selon les paramètres GET."""
    from datetime import datetime
    transfers = _scoped_transfers(request.user)
    status = request.GET.get('status', '')
    if status:
        transfers = transfers.filter(status=status)
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    if date_from:
        try:
            transfers = transfers.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            transfers = transfers.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    return transfers.select_related('from_warehouse', 'to_warehouse', 'created_by')


@login_required
@seller_or_admin_required
def dash_transfers_export_pdf(request):
    """Export PDF des transferts filtrés"""
    from django.http import HttpResponse
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    import io

    transfers = _filtered_transfers(request)[:500]
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    p.setFont('Helvetica-Bold', 16)
    p.drawString(_pdf_brand(p, 15*mm, height - 18*mm), height - 18*mm, 'Comptoir — Mouvements entre entrepôts')
    p.setFont('Helvetica', 9)
    p.drawString(15*mm, height - 24*mm, f'Généré le {timezone.now().strftime("%d/%m/%Y à %H:%M")} — {transfers.count()} transfert(s)')
    p.line(15*mm, height - 27*mm, width - 15*mm, height - 27*mm)

    y = height - 36*mm
    p.setFont('Helvetica-Bold', 8)
    headers = ['Référence', 'De', 'Vers', 'Produits', 'Statut', 'Créé le', 'Par']
    cols = [15, 50, 95, 140, 165, 200, 235]
    for x, h in zip(cols, headers):
        p.drawString(x*mm, y, h)
    p.line(15*mm, y - 2*mm, width - 15*mm, y - 2*mm)
    y -= 8*mm

    p.setFont('Helvetica', 8)
    for t in transfers:
        if y < 15*mm:
            p.showPage()
            y = height - 20*mm
            p.setFont('Helvetica', 8)
        row = [
            t.reference,
            t.from_warehouse.name[:25],
            t.to_warehouse.name[:25],
            str(t.items.count()),
            t.get_status_display(),
            t.created_at.strftime('%d/%m/%Y'),
            (t.created_by.display_name if t.created_by else '—')[:20],
        ]
        for x, val in zip(cols, row):
            p.drawString(x*mm, y, str(val))
        y -= 6*mm

    p.showPage()
    p.save()
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="mouvements_entrepots.pdf"'
    return response


@login_required
@seller_or_admin_required
def dash_transfers_export_excel(request):
    """Export Excel des transferts filtrés"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font

    transfers = _filtered_transfers(request)[:1000]
    wb = Workbook()
    ws = wb.active
    ws.title = 'Mouvements'
    ws.append(['Référence', 'De', 'Vers', 'Produits', 'Quantité totale', 'Statut', 'Créé le', 'Par'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for t in transfers:
        ws.append([
            t.reference,
            t.from_warehouse.name,
            t.to_warehouse.name,
            t.items.count(),
            sum(i.quantity for i in t.items.all()),
            t.get_status_display(),
            t.created_at.strftime('%d/%m/%Y %H:%M'),
            t.created_by.display_name if t.created_by else '',
        ])
    for col, w in zip('ABCDEFGH', [12, 25, 25, 10, 14, 12, 16, 18]):
        ws.column_dimensions[col].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="mouvements_entrepots.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_transfer_action(request, pk, action):
    """Confirmer / recevoir / annuler un transfert"""
    from inventory.models import StockTransfer
    from django.db import transaction
    transfer = get_object_or_404(StockTransfer.objects.select_related('store', 'from_warehouse', 'to_warehouse'), pk=pk)
    # Confirmer/annuler concerne l'entrepôt source, réceptionner l'entrepôt destination
    concerned = transfer.to_warehouse if action == 'receive' else transfer.from_warehouse
    if not _stock_access(request.user, transfer.store, 'stock.transfer', concerned):
        django_messages.error(request, "Accès refusé.")
        return redirect('dashboard:transfers')

    if request.method == 'POST':
        missing = transfer.missing_stock() if action == 'confirm' and transfer.status == 'draft' else []
        if missing:
            django_messages.error(request, 'Stock insuffisant dans ' + transfer.from_warehouse.name + ' : ' + ', '.join(
                f'{name} ({available} dispo / {needed} demandé)' for name, available, needed in missing))
            return redirect('dashboard:transfers')
        with transaction.atomic():
            done = (action == 'confirm' and transfer.confirm(user=request.user)) \
                or (action == 'receive' and transfer.receive(user=request.user)) \
                or (action == 'cancel' and transfer.cancel(user=request.user))
        if done and action == 'confirm':
            django_messages.success(request, f'{transfer.reference} confirmé — stock sorti de {transfer.from_warehouse.name}.')
        elif done and action == 'receive':
            django_messages.success(request, f'{transfer.reference} reçu dans {transfer.to_warehouse.name}.')
        elif done and action == 'cancel':
            django_messages.success(request, f'{transfer.reference} annulé.')
        else:
            django_messages.error(request, 'Action impossible dans cet état.')
    return redirect('dashboard:transfers')


@login_required
@seller_or_admin_required
def dash_stores(request):
    """Liste des boutiques de l'utilisateur"""
    from inventory.models import Warehouse
    stores = request.user.stores.all()
    warehouses = Warehouse.objects.filter(store__owner=request.user, is_active=True)
    return render(request, 'dashboard/stores.html', {
        'stores': stores,
        'warehouses': warehouses,
    })


# ======== EMPLOYÉS & RÔLES ========
@login_required
@seller_or_admin_required
def dash_employees(request):
    """Page d'accueil Employés : 3 cartes (Employés, Rôles, Paie)"""
    store = getattr(request.user, 'store', None)
    if not store:
        django_messages.error(request, "Vous devez avoir une boutique.")
        return redirect('dashboard:index')
    return render(request, 'dashboard/employees.html', {
        'members_count': store.members.filter(is_active=True).count(),
        'roles_count': store.roles.filter(is_active=True).count(),
        'payslips_count': store.members.filter(payslips__isnull=False).values('payslips').distinct().count(),
    })


@login_required
@seller_or_admin_required
def dash_employees_list(request):
    """Liste des employés avec ajout"""
    from store.models import StoreMember, StoreRole
    store = getattr(request.user, 'store', None)
    if not store:
        django_messages.error(request, "Vous devez avoir une boutique.")
        return redirect('dashboard:index')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        password_confirm = request.POST.get('password_confirm', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        role_id = request.POST.get('role') or None
        warehouse_id = request.POST.get('warehouse') or None

        if not username:
            django_messages.error(request, "Le nom d'utilisateur est requis.")
            return redirect('dashboard:employees_list')

        user = User.objects.filter(username=username).first()
        if user is None:
            # Créer le compte employé avec mot de passe
            from accounts.passwords import password_problem
            pw_error = password_problem(password, username)
            if pw_error:
                django_messages.error(request, f"Mot de passe de l'employé : {pw_error}")
                return redirect('dashboard:employees_list')
            if password != password_confirm:
                django_messages.error(request, "Les mots de passe ne correspondent pas.")
                return redirect('dashboard:employees_list')
            user = User.objects.create_user(
                username=username, password=password,
                first_name=first_name, last_name=last_name,
                role='buyer',
            )
            # Envoyer les identifiants par email si l'employé a un email
            if user.email:
                from messaging.utils import notify
                notify(
                    user, 'account',
                    f'Vous avez rejoint {store.name}',
                    f'{request.user.display_name} vous a ajouté à son équipe.\n\nVos identifiants de connexion :\nNom d\'utilisateur : {username}\nMot de passe : {password}\n\nConnectez-vous pour accéder au tableau de bord.',
                    url='/compte/connexion/',
                    send_email=True,
                    email_subject=f'Vos identifiants {store.name} — Comptoir',
                )
            django_messages.success(request, f'Compte créé pour {user.display_name}.')
        elif user == request.user:
            django_messages.error(request, "Vous êtes déjà le propriétaire.")
            return redirect('dashboard:employees_list')

        salary = int(request.POST.get('salary', 0) or 0)
        member, created = StoreMember.objects.get_or_create(
            store=store, user=user,
            defaults={'role_id': role_id, 'warehouse_id': warehouse_id, 'salary': salary}
        )
        if not created:
            member.role_id = role_id
            member.warehouse_id = warehouse_id
            member.salary = salary
            member.is_active = True
            member.save()
        django_messages.success(request, f'{user.display_name} fait maintenant partie de votre équipe.')
        return redirect('dashboard:employees_list')

    members = store.members.select_related('user', 'role', 'warehouse')
    warehouse_id = request.GET.get('warehouse', '')
    if warehouse_id:
        members = members.filter(warehouse_id=warehouse_id)
    search_query = request.GET.get('q', '').strip()
    if search_query:
        from django.db.models import Q
        members = members.filter(
            Q(user__username__icontains=search_query) |
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query)
        )

    return render(request, 'dashboard/employees_list.html', {
        'members': members,
        'owner': store.owner,
        'roles': store.roles.filter(is_active=True),
        'warehouses': store.warehouses.filter(is_active=True),
        'warehouse_filter': warehouse_id,
        'search_query': search_query,
    })


@login_required
@seller_or_admin_required
def dash_employee_toggle(request, pk):
    """Activer/désactiver un employé"""
    from store.models import StoreMember
    member = get_object_or_404(StoreMember, pk=pk, store=request.user.store)
    if request.method == 'POST':
        member.is_active = not member.is_active
        member.save()
        django_messages.success(request, f'{member.user.display_name} {"activé" if member.is_active else "désactivé"}.')
    return redirect('dashboard:employees_list')


@login_required
@seller_or_admin_required
def dash_roles(request):
    """Gestion des rôles et permissions"""
    from store.models import StoreRole
    store = getattr(request.user, 'store', None)
    if not store:
        django_messages.error(request, "Vous devez avoir une boutique.")
        return redirect('dashboard:index')

    if request.method == 'POST':
        role_id = request.POST.get('role_id')
        permissions = request.POST.getlist('permissions')
        if role_id:
            role = get_object_or_404(StoreRole, pk=role_id, store=store)
            role.name = request.POST.get('name', role.name)
            role.permissions = permissions
            role.save()
            django_messages.success(request, f'Rôle "{role.name}" mis à jour.')
        else:
            StoreRole.objects.create(
                store=store,
                name=request.POST.get('name'),
                permissions=permissions,
            )
            django_messages.success(request, 'Rôle créé !')
        return redirect('dashboard:roles')

    return render(request, 'dashboard/roles.html', {
        'roles': store.roles.all(),
        'permission_choices': StoreRole.PERMISSION_CHOICES,
    })


@login_required
@seller_or_admin_required
def dash_employee_detail(request, pk):
    """Détail d'un employé : infos + bulletins de paie"""
    from store.models import StoreMember
    member = get_object_or_404(StoreMember, pk=pk, store=request.user.store)
    return render(request, 'dashboard/employee_detail.html', {
        'member': member,
        'payslips': member.payslips.all(),
    })


# ======== PAIE DES EMPLOYÉS ========
@login_required
@seller_or_admin_required
def dash_payroll(request):
    """Liste des bulletins de paie"""
    from store.models import Payslip
    store = getattr(request.user, 'store', None)
    if not store:
        django_messages.error(request, "Vous devez avoir une boutique.")
        return redirect('dashboard:index')

    payslips = Payslip.objects.filter(member__store=store).select_related('member', 'member__user')

    month = request.GET.get('month', '')
    year = request.GET.get('year', '')
    if month:
        payslips = payslips.filter(month=month)
    if year:
        payslips = payslips.filter(year=year)

    return render(request, 'dashboard/payroll.html', {
        'payslips': payslips,
        'month_filter': month,
        'year_filter': year,
        'months': [(i, timezone.datetime(2000, i, 1).strftime('%B')) for i in range(1, 13)],
        'years': range(timezone.now().year - 2, timezone.now().year + 1),
        'members': store.members.filter(is_active=True).select_related('user'),
    })


@login_required
@seller_or_admin_required
def dash_payslip_create(request):
    """Créer un bulletin de paie"""
    from store.models import Payslip, StoreMember
    store = getattr(request.user, 'store', None)
    if not store:
        return redirect('dashboard:index')

    if request.method == 'POST':
        from store.models import PayslipLine
        member = get_object_or_404(StoreMember, pk=request.POST.get('member'), store=store)
        month = int(request.POST.get('month'))
        year = int(request.POST.get('year'))
        # Vérifier si déjà existant
        if Payslip.objects.filter(member=member, month=month, year=year).exists():
            django_messages.error(request, 'Un bulletin existe déjà pour cet employé ce mois-ci.')
            return redirect('dashboard:payroll')
        payslip = Payslip.objects.create(
            member=member,
            month=month,
            year=year,
            base_salary=int(request.POST.get('base_salary', member.salary or 0)),
            notes=request.POST.get('notes', ''),
            created_by=request.user,
        )
        # Lignes de primes et retenues
        line_types = request.POST.getlist('line_type[]')
        line_labels = request.POST.getlist('line_label[]')
        line_amounts = request.POST.getlist('line_amount[]')
        for i, ltype in enumerate(line_types):
            label = line_labels[i].strip() if i < len(line_labels) else ''
            amount = int(line_amounts[i]) if i < len(line_amounts) and line_amounts[i] else 0
            if label and amount > 0:
                PayslipLine.objects.create(payslip=payslip, line_type=ltype, label=label, amount=amount)
        django_messages.success(request, f'Bulletin {payslip.reference} créé !')
    return redirect('dashboard:payroll')


@login_required
@seller_or_admin_required
def dash_payslip_action(request, pk, action):
    """Valider / marquer payé un bulletin"""
    from store.models import Payslip
    payslip = get_object_or_404(Payslip, pk=pk, member__store=request.user.store)
    if request.method == 'POST':
        if action == 'validate' and payslip.status == 'draft':
            payslip.status = 'validated'
            payslip.save()
            django_messages.success(request, f'{payslip.reference} validé.')
        elif action == 'pay' and payslip.status == 'validated':
            payslip.mark_paid()
            django_messages.success(request, f'{payslip.reference} marqué comme payé.')
    return redirect('dashboard:payroll')


# ======== GESTION DE STOCK ========
def _inventory_store_ids(request):
    """Boutiques dont l'utilisateur peut voir le stock (None = admin, tout voir)."""
    user = request.user
    if user.is_superuser or user.role == 'admin':
        return None
    ids = set(user.stores.values_list('pk', flat=True))
    ids |= set(user.store_memberships.filter(is_active=True).values_list('store_id', flat=True))
    return ids


def _filtered_movements(request):
    """Mouvements filtrés selon les paramètres GET (page inventaire + exports).
    Retourne (queryset, entrepôt courant, store_ids)."""
    from catalog.models import StockMovement
    from inventory.models import Warehouse
    from datetime import datetime
    store_ids = _inventory_store_ids(request)
    movements = StockMovement.objects.all()
    if store_ids is not None:
        movements = movements.filter(store_id__in=store_ids)

    current_warehouse = None
    warehouse_id = request.GET.get('warehouse', '')
    if warehouse_id.isdigit():
        warehouses = Warehouse.objects.all()
        if store_ids is not None:
            warehouses = warehouses.filter(store_id__in=store_ids)
        current_warehouse = warehouses.filter(pk=warehouse_id).first()
        # Entrepôt inconnu ou d'une autre boutique : aucun résultat plutôt que tout afficher
        movements = movements.filter(warehouse=current_warehouse) if current_warehouse else movements.none()

    q = request.GET.get('q', '').strip()
    if q:
        movements = movements.filter(Q(product__name__icontains=q) | Q(reference__icontains=q) | Q(reason__icontains=q))
    mtype = request.GET.get('type', '')
    if mtype:
        movements = movements.filter(movement_type=mtype)
    for param, lookup in (('from', 'created_at__date__gte'), ('to', 'created_at__date__lte')):
        value = request.GET.get(param, '')
        if value:
            try:
                movements = movements.filter(**{lookup: datetime.strptime(value, '%Y-%m-%d').date()})
            except ValueError:
                pass
    return movements.select_related('product', 'created_by', 'warehouse'), current_warehouse, store_ids


@login_required
@seller_or_admin_required
def dash_inventory(request):
    """Page inventaire : mouvements de stock filtrés + stock actuel d'un entrepôt"""
    from catalog.models import StockMovement
    from inventory.models import Warehouse
    from django.core.paginator import Paginator
    from django.db.models.functions import Coalesce

    movements, current_warehouse, store_ids = _filtered_movements(request)
    stats = movements.aggregate(
        entries=Coalesce(Sum('quantity', filter=Q(quantity__gt=0)), 0),
        exits=Coalesce(Sum('quantity', filter=Q(quantity__lt=0)), 0),
        count=Count('id'),
    )
    stats['exits'] = -stats['exits']
    stats['net'] = stats['entries'] - stats['exits']

    page_obj = Paginator(movements, 25).get_page(request.GET.get('page'))

    warehouses = Warehouse.objects.filter(is_active=True).select_related('store')
    if store_ids is not None:
        warehouses = warehouses.filter(store_id__in=store_ids)

    tab = request.GET.get('tab', 'movements')
    stocks, stock_stats, products = None, None, None
    if current_warehouse:
        stocks = current_warehouse.stocks.select_related('product').order_by('product__name')
        stock_stats = {
            'products': stocks.count(),
            'units': stocks.aggregate(t=Coalesce(Sum('quantity'), 0))['t'],
            'low': stocks.filter(quantity__gt=0, quantity__lte=F('product__low_stock_threshold')).count(),
            'out': stocks.filter(quantity=0).count(),
        }
        q = request.GET.get('q', '').strip()
        if q and tab == 'stock':
            stocks = stocks.filter(product__name__icontains=q)
        products = current_warehouse.store.products.filter(is_active=True).order_by('name')
    else:
        tab = 'movements'

    # Paramètres GET sans la page, pour garder les filtres dans la pagination
    params = request.GET.copy()
    params.pop('page', None)

    return render(request, 'dashboard/inventory.html', {
        'movements': page_obj,
        'page_obj': page_obj,
        'stats': stats,
        'search_query': request.GET.get('q', '').strip(),
        'warehouse': current_warehouse,
        'warehouse_param': request.GET.get('warehouse', ''),
        'warehouses': warehouses,
        'type_filter': request.GET.get('type', ''),
        'date_from': request.GET.get('from', ''),
        'date_to': request.GET.get('to', ''),
        'type_choices': StockMovement.TYPE_CHOICES,
        'tab': tab,
        'stocks': stocks,
        'stock_stats': stock_stats,
        'products': products,
        'base_query': params.urlencode(),
    })


@login_required
@seller_or_admin_required
def dash_stock_adjust(request, pk):
    """Ajustement / entrée / sortie de stock via modale"""
    from inventory.models import Warehouse
    product = get_object_or_404(Product, pk=pk)
    warehouse = None
    warehouse_id = request.POST.get('warehouse')
    if warehouse_id:
        warehouse = get_object_or_404(Warehouse, pk=warehouse_id, store=product.store)
    if not _stock_access(request.user, product.store, 'stock.adjust', warehouse):
        django_messages.error(request, "Vous n'avez pas le droit d'ajuster ce stock.")
        return redirect('dashboard:products')

    if request.method == 'POST':
        movement_type = request.POST.get('movement_type', 'adjustment')
        qty = int(request.POST.get('quantity', 0))
        reason = request.POST.get('reason', '').strip()
        threshold = request.POST.get('low_stock_threshold')

        if threshold is not None and threshold != '':
            product.low_stock_threshold = max(0, int(threshold))
            product.save(update_fields=['low_stock_threshold'])

        if qty > 0:
            if movement_type == 'out':
                qty = -qty
            product.adjust_stock(qty, movement_type, user=request.user, reason=reason, warehouse=warehouse)
            django_messages.success(request, f'Stock de "{product.name}" mis à jour : {product.stock} unités.')
        else:
            django_messages.error(request, 'La quantité doit être supérieure à 0.')

    return _safe_back(request, 'dashboard:products')


@login_required
@seller_or_admin_required
def dash_movements_export_pdf(request):
    """Export PDF de la liste filtrée des mouvements"""
    from django.http import HttpResponse
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    import io

    movements = _filtered_movements(request)[0][:500]

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    p.setFont('Helvetica-Bold', 16)
    p.drawString(_pdf_brand(p, 15*mm, height - 18*mm), height - 18*mm, 'Comptoir — Mouvements de stock')
    p.setFont('Helvetica', 9)
    p.drawString(15*mm, height - 24*mm, f'Généré le {timezone.now().strftime("%d/%m/%Y à %H:%M")} — {movements.count()} mouvement(s)')
    p.line(15*mm, height - 27*mm, width - 15*mm, height - 27*mm)

    y = height - 36*mm
    p.setFont('Helvetica-Bold', 8)
    headers = ['Date', 'Produit', 'Type', 'Qté', 'Avant', 'Après', 'Motif', 'Référence', 'Par']
    cols = [15, 40, 95, 125, 140, 155, 170, 215, 245]
    for x, h in zip(cols, headers):
        p.drawString(x*mm, y, h)
    p.line(15*mm, y - 2*mm, width - 15*mm, y - 2*mm)
    y -= 8*mm

    p.setFont('Helvetica', 8)
    for m in movements:
        if y < 15*mm:
            p.showPage()
            y = height - 20*mm
            p.setFont('Helvetica', 8)
        row = [
            m.created_at.strftime('%d/%m/%y %H:%M'),
            m.product.name[:30],
            m.get_movement_type_display(),
            f'{m.quantity:+d}',
            str(m.stock_before),
            str(m.stock_after),
            (m.reason or '—')[:25],
            (m.reference or '—')[:15],
            (m.created_by.display_name if m.created_by else '—')[:15],
        ]
        for x, val in zip(cols, row):
            p.drawString(x*mm, y, str(val))
        y -= 6*mm

    p.showPage()
    p.save()
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="mouvements_stock.pdf"'
    return response


@login_required
@seller_or_admin_required
def dash_movements_export_excel(request):
    """Export Excel de la liste filtrée des mouvements"""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font

    movements = _filtered_movements(request)[0][:1000]

    wb = Workbook()
    ws = wb.active
    ws.title = 'Mouvements'
    headers = ['Date', 'Produit', 'Type', 'Quantité', 'Stock avant', 'Stock après', 'Motif', 'Référence', 'Effectué par']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for m in movements:
        ws.append([
            m.created_at.strftime('%d/%m/%Y %H:%M'),
            m.product.name,
            m.get_movement_type_display(),
            m.quantity,
            m.stock_before,
            m.stock_after,
            m.reason or '',
            m.reference or '',
            m.created_by.display_name if m.created_by else '',
        ])
    for col, w in zip('ABCDEFGHI', [16, 30, 14, 10, 12, 12, 30, 15, 15]):
        ws.column_dimensions[col].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="mouvements_stock.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_movement_pdf(request, pk):
    """Export PDF d'un mouvement de stock"""
    from catalog.models import StockMovement
    from django.http import HttpResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    import io

    m = get_object_or_404(StockMovement, pk=pk)
    is_admin = request.user.is_superuser or request.user.role == 'admin'
    if not is_admin and m.store != request.user.store:
        django_messages.error(request, "Accès refusé.")
        return redirect('dashboard:inventory')

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    p.setFont('Helvetica-Bold', 20)
    p.drawString(_pdf_brand(p, 20*mm, height - 25*mm, 10), height - 25*mm, 'Comptoir — Mouvement de stock')
    p.setLineWidth(1)
    p.line(20*mm, height - 30*mm, width - 20*mm, height - 30*mm)

    rows = [
        ('Référence mouvement', f'#{m.id}'),
        ('Produit', m.product.name),
        ('Type', m.get_movement_type_display()),
        ('Quantité', f'{m.quantity:+d}'),
        ('Stock avant', str(m.stock_before)),
        ('Stock après', str(m.stock_after)),
        ('Motif', m.reason or '—'),
        ('Référence', m.reference or '—'),
        ('Effectué par', m.created_by.display_name if m.created_by else '—'),
        ('Date', m.created_at.strftime('%d/%m/%Y %H:%M')),
    ]
    y = height - 45*mm
    for label, value in rows:
        p.setFont('Helvetica', 10)
        p.drawString(25*mm, y, label)
        p.setFont('Helvetica-Bold', 11)
        p.drawString(80*mm, y, str(value))
        y -= 10*mm

    p.setFont('Helvetica', 8)
    p.drawString(20*mm, 15*mm, f'Document généré le {timezone.now().strftime("%d/%m/%Y à %H:%M")} — Comptoir')
    p.showPage()
    p.save()
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="mouvement_{m.id}.pdf"'
    return response


@login_required
@seller_or_admin_required
def dash_movement_excel(request, pk):
    """Export Excel d'un mouvement de stock"""
    from catalog.models import StockMovement
    from django.http import HttpResponse
    from openpyxl import Workbook

    m = get_object_or_404(StockMovement, pk=pk)
    is_admin = request.user.is_superuser or request.user.role == 'admin'
    if not is_admin and m.store != request.user.store:
        django_messages.error(request, "Accès refusé.")
        return redirect('dashboard:inventory')

    wb = Workbook()
    ws = wb.active
    ws.title = 'Mouvement'
    ws.append(['Champ', 'Valeur'])
    for cell in ws[1]:
        cell.font = cell.font.copy(bold=True)
    rows = [
        ('Référence mouvement', f'#{m.id}'),
        ('Produit', m.product.name),
        ('Type', m.get_movement_type_display()),
        ('Quantité', m.quantity),
        ('Stock avant', m.stock_before),
        ('Stock après', m.stock_after),
        ('Motif', m.reason or '—'),
        ('Référence', m.reference or '—'),
        ('Effectué par', m.created_by.display_name if m.created_by else '—'),
        ('Date', m.created_at.strftime('%d/%m/%Y %H:%M')),
    ]
    for row in rows:
        ws.append(list(row))
    ws.column_dimensions['A'].width = 25
    ws.column_dimensions['B'].width = 40

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="mouvement_{m.id}.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_customers(request):
    # Si l'utilisateur est un vendeur, afficher uniquement ses clients
    if request.user.is_seller and (request.user.store is not None):
        customers = User.objects.filter(
            orders__items__product__store__owner=request.user
        ).distinct().annotate(
            order_count=Count('orders', filter=Q(orders__items__product__store__owner=request.user), distinct=True),
            total_spent=Sum('orders__total_amount', filter=Q(orders__items__product__store__owner=request.user))
        ).order_by('-date_joined')
    else:
        customers = User.objects.filter(role='buyer').annotate(
            order_count=Count('orders'), total_spent=Sum('orders__total_amount')
        ).order_by('-date_joined')

    # Recherche
    q = request.GET.get('q', '').strip()
    if q:
        customers = customers.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )

    customers_list = list(customers)
    total_spent_all = sum(c.total_spent or 0 for c in customers_list)
    total_orders_all = sum(c.order_count or 0 for c in customers_list)

    return render(request, 'dashboard/customers.html', {
        'customers': customers_list,
        'total': len(customers_list),
        'new_30d': sum(1 for c in customers_list if c.date_joined >= timezone.now() - timedelta(days=30)),
        'total_spent_all': total_spent_all,
        'avg_spent': int(total_spent_all / len(customers_list)) if customers_list else 0,
        'search_query': q,
    })


@login_required
@seller_or_admin_required
def dash_customers_export(request):
    """Export Excel de la liste des clients"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from django.http import HttpResponse

    if request.user.is_seller and (request.user.store is not None):
        customers = User.objects.filter(
            orders__items__product__store__owner=request.user
        ).distinct().annotate(
            order_count=Count('orders', filter=Q(orders__items__product__store__owner=request.user), distinct=True),
            total_spent=Sum('orders__total_amount', filter=Q(orders__items__product__store__owner=request.user))
        )
    else:
        customers = User.objects.filter(role='buyer').annotate(
            order_count=Count('orders'), total_spent=Sum('orders__total_amount')
        )

    q = request.GET.get('q', '').strip()
    if q:
        customers = customers.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )

    wb = Workbook()
    ws = wb.active
    ws.title = 'Clients'
    ws.append(['Nom', 'Email', 'Téléphone', 'Ville', 'Commandes', 'Total dépensé (F)', 'Inscrit le'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for c in customers:
        ws.append([
            c.display_name, c.email, c.phone or '', c.city or '',
            c.order_count or 0, int(c.total_spent or 0),
            c.date_joined.strftime('%d/%m/%Y'),
        ])
    for col, w in zip('ABCDEFG', [25, 30, 15, 15, 12, 18, 12]):
        ws.column_dimensions[col].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="clients.xlsx"'
    wb.save(response)
    return response


@login_required
@seller_or_admin_required
def dash_store(request):
    store = getattr(request.user, 'store', None)
    if not store:
        django_messages.error(request, 'Vous n\'avez pas de boutique.')
        return redirect('dashboard:index')

    # Créer une nouvelle boutique
    if request.method == 'POST' and request.POST.get('action') == 'create_store':
        from inventory.models import Warehouse
        new_store = Store.objects.create(
            owner=request.user,
            name=request.POST.get('name'),
            city=request.POST.get('city', 'Douala'),
            phone=request.POST.get('phone', ''),
            is_active='is_active' in request.POST,
        )
        # Lier à l'entrepôt choisi
        warehouse_id = request.POST.get('warehouse')
        if warehouse_id:
            wh = Warehouse.objects.filter(pk=warehouse_id, store__owner=request.user).first()
            if wh:
                wh.linked_stores.add(new_store)
        django_messages.success(request, f'Boutique "{new_store.name}" créée !')
        return redirect('dashboard:stores')

    if request.method == 'POST':
        store.name = request.POST.get('name', store.name)
        store.description = request.POST.get('description', '')
        store.phone = request.POST.get('phone', '')
        store.whatsapp = request.POST.get('whatsapp', '')
        store.email = request.POST.get('email', '')
        store.address = request.POST.get('address', '')
        store.city = request.POST.get('city', '')
        store.latitude = request.POST.get('latitude') or None
        store.longitude = request.POST.get('longitude') or None
        store.opening_hours = request.POST.get('opening_hours', '')
        store.facebook = request.POST.get('facebook', '')
        store.instagram = request.POST.get('instagram', '')
        if request.FILES.get('logo'): store.logo = request.FILES['logo']
        if request.FILES.get('banner'): store.banner = request.FILES['banner']
        store.save()
        django_messages.success(request, 'Boutique mise à jour !')

    # Score de complétion du profil
    fields = [store.name, store.description, store.phone, store.email, store.address,
              store.city, store.logo, store.banner, store.whatsapp, store.opening_hours]
    completion = int(sum(1 for f in fields if f) / len(fields) * 100)

    return render(request, 'dashboard/store.html', {
        'store': store,
        'completion': completion,
        'products_count': store.products.count(),
        'total_sales': store.total_sales,
        'rating': store.rating,
    })


@login_required
@seller_or_admin_required
def dash_search(request):
    """Recherche globale du dashboard : commandes, produits, clients, factures (selon les droits)."""
    from invoicing.models import Invoice
    q = request.GET.get('q', '').strip()[:100]
    store = access.acting_store(request.user)
    admin = access.is_admin(request.user)
    can = lambda perm: admin or (store is not None and access.has_perm(request.user, store, perm))  # noqa: E731
    results = {'orders': [], 'products': [], 'customers': [], 'invoices': []}
    if len(q) >= 2:
        limit = access.member_warehouse_id(request.user, store) if store is not None else None
        if can('orders.view'):
            mine = OrderItem.objects.all() if store is None else OrderItem.objects.filter(store=store)
            if limit:
                mine = mine.filter(warehouse_id=limit)
            orders = Order.objects.filter(pk__in=mine.values('order')).filter(
                Q(order_number__icontains=q) | Q(shipping_name__icontains=q) | Q(shipping_phone__icontains=q)
                | Q(buyer__username__icontains=q) | Q(buyer__email__icontains=q))
            exact = orders.filter(order_number__iexact=q.lstrip('#')).first()
            if exact:
                return redirect('dashboard:order_detail', order_number=exact.order_number)
            results['orders'] = list(orders.select_related('buyer').order_by('-created_at')[:8])
        if can('products.view'):
            products = Product.objects.all() if store is None else store.products.all()
            results['products'] = list(products.filter(Q(name__icontains=q) | Q(sku__icontains=q)).order_by('name')[:8])
        if can('customers.view'):
            buyers = User.objects.filter(orders__items__store=store) if store is not None else User.objects.filter(role='buyer')
            results['customers'] = list(buyers.filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(username__icontains=q)
                | Q(email__icontains=q) | Q(phone__icontains=q)).distinct()[:8])
        if can('invoicing.view') and store is not None:
            invoices = Invoice.objects.filter(store=store)
            if limit:
                invoices = invoices.filter(warehouse_id=limit)
            results['invoices'] = list(invoices.filter(Q(invoice_number__icontains=q) | Q(customer_name__icontains=q))
                                       .order_by('-created_at')[:8])
    return render(request, 'dashboard/search.html', {
        'q': q, 'results': results, 'total': sum(len(v) for v in results.values()),
    })


ASSISTANT_SUGGESTIONS = [
    'Résume mes ventes des 30 derniers jours et compare au mois précédent',
    'Quels produits me rapportent le plus de marge ?',
    'Quels produits dois-je réapprovisionner en priorité ?',
    'Quelles commandes sont en attente depuis plus de 2 jours ?',
    'Qui sont mes meilleurs clients cette année ?',
    'Quelles factures sont en retard de paiement ?',
]


@login_required
@seller_or_admin_required
def dash_assistant(request):
    """Assistant IA : briefing du jour + conversation avec Claude (outils sur les vraies données)."""
    from .ai_assistant import is_configured, render_markdown
    from .assistant import get_briefing
    from .models import AssistantConversation
    store = access.acting_store(request.user)
    conversations = AssistantConversation.objects.filter(user=request.user, store=store)
    current = None
    if request.GET.get('c', '').isdigit():
        current = conversations.filter(pk=request.GET['c']).first()
    elif not request.GET.get('new'):
        current = conversations.first()
    transcript = []
    if current:
        for entry in current.transcript:
            transcript.append({**entry, 'html': render_markdown(entry['text']) if entry['role'] == 'assistant' else ''})
    return render(request, 'dashboard/assistant.html', {
        'ai_enabled': is_configured(),
        'model_name': settings.ASSISTANT_MODEL,
        'conversations': conversations[:30],
        'current': current,
        'transcript': transcript,
        'suggestions': ASSISTANT_SUGGESTIONS,
        'briefing': get_briefing(store) if store else None,
    })


@login_required
@seller_or_admin_required
def dash_assistant_ask(request):
    """Pose une question (JSON). Avec une clé API : Claude + outils ; sinon l'assistant par mots-clés."""
    from .ai_assistant import ask as ai_ask, is_configured, render_markdown
    from .models import AssistantConversation
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST attendu'}, status=405)
    question = request.POST.get('question', '').strip()
    if not question:
        return JsonResponse({'ok': False, 'error': 'Posez une question.'}, status=400)
    store = access.acting_store(request.user)

    if not is_configured():
        from .assistant import ask as keyword_ask
        text = keyword_ask(question, store, user=request.user) if store else "Aucune boutique associée à ce compte."
        text = re.sub('[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2705\u274C]\ufe0f?', '', text)
        return JsonResponse({'ok': True, 'html': str(render_markdown(text)), 'tools': [], 'conversation': None, 'mode': 'simple'})

    conv_id = request.POST.get('conversation', '')
    conversation = None
    if conv_id.isdigit():
        conversation = AssistantConversation.objects.filter(pk=conv_id, user=request.user, store=store).first()
    if conversation is None:
        conversation = AssistantConversation.objects.create(user=request.user, store=store)
    result = ai_ask(conversation, question, request.user)
    if result['error']:
        return JsonResponse({'ok': False, 'error': result['error'], 'conversation': conversation.pk}, status=502)
    return JsonResponse({'ok': True, 'html': str(render_markdown(result['text'])), 'tools': result['tools'],
                         'conversation': conversation.pk, 'title': conversation.title, 'mode': 'ai'})


@login_required
@seller_or_admin_required
def dash_assistant_delete(request, pk):
    from .models import AssistantConversation
    if request.method == 'POST':
        AssistantConversation.objects.filter(pk=pk, user=request.user).delete()
    return redirect(f"{reverse('dashboard:assistant')}?new=1")


@login_required
@seller_or_admin_required
def dash_notifications(request):
    """Centre de notifications du dashboard (même page que sur le site, dans la mise en page vendeur)."""
    from messaging.views import notification_center
    return notification_center(request, layout='dashboard/base.html')


@login_required
@seller_or_admin_required
def dash_settings(request):
    """Page de paramétrage : boutique, facturation, compte"""
    from invoicing.models import InvoiceSettings
    store = getattr(request.user, 'store', None)
    if not store:
        django_messages.error(request, "Vous devez avoir une boutique.")
        return redirect('dashboard:index')

    invoice_settings, _ = InvoiceSettings.objects.get_or_create(
        store=store,
        defaults={
            'company_name': store.name,
            'address': store.address,
            'city': store.city,
            'phone': store.phone,
            'email': store.email,
        }
    )

    if request.method == 'POST':
        section = request.POST.get('section')

        if section == 'invoicing':
            s = invoice_settings
            s.company_name = request.POST.get('company_name', '')
            s.tax_id = request.POST.get('tax_id', '')
            s.registration_number = request.POST.get('registration_number', '')
            s.address = request.POST.get('address', '')
            s.city = request.POST.get('city', '')
            s.country = request.POST.get('country', 'Cameroun')
            s.phone = request.POST.get('phone', '')
            s.email = request.POST.get('email', '')
            s.website = request.POST.get('website', '')
            s.apply_tva = 'apply_tva' in request.POST
            s.tva_rate = request.POST.get('tva_rate', 19.25)
            s.invoice_prefix = request.POST.get('invoice_prefix', 'FAC')
            s.header_text = request.POST.get('header_text', '')
            s.footer_text = request.POST.get('footer_text', '')
            s.payment_terms = request.POST.get('payment_terms', '')
            s.bank_details = request.POST.get('bank_details', '')
            s.signature_name = request.POST.get('signature_name', '')
            s.signature_title = request.POST.get('signature_title', '')
            if request.FILES.get('logo'):
                s.logo = request.FILES['logo']
            if request.FILES.get('signature'):
                s.signature = request.FILES['signature']
            s.save()
            django_messages.success(request, 'Paramètres de facturation enregistrés.')

        elif section == 'payments':
            from billing.models import PaymentConfig
            from billing.payments import save_config
            cfg, _ = PaymentConfig.objects.get_or_create(user=store.owner)
            errors = save_config(cfg, request.POST)
            for e in errors:
                django_messages.error(request, e)
            if not errors:
                django_messages.success(request, 'Moyens de paiement enregistrés. Vos clients les voient dès maintenant.')

        elif section == 'notifications':
            from messaging.models import NotificationPreference
            prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
            prefs.internal_enabled = 'internal_enabled' in request.POST
            prefs.email_order = 'email_order' in request.POST
            prefs.email_rfq = 'email_rfq' in request.POST
            prefs.email_invoice = 'email_invoice' in request.POST
            prefs.email_payment = 'email_payment' in request.POST
            prefs.email_stock = 'email_stock' in request.POST
            prefs.email_account = 'email_account' in request.POST
            prefs.email_marketing = 'email_marketing' in request.POST
            prefs.whatsapp_orders = 'whatsapp_orders' in request.POST
            prefs.save()
            django_messages.success(request, 'Préférences de notification enregistrées.')

        elif section == 'account':
            user = request.user
            user.first_name = request.POST.get('first_name', '')
            user.last_name = request.POST.get('last_name', '')
            user.email = request.POST.get('email', '')
            user.phone = request.POST.get('phone', '')
            user.save()
            new_password = request.POST.get('new_password', '').strip()
            if new_password:
                if not user.check_password(request.POST.get('current_password', '')):
                    django_messages.error(request, 'Mot de passe actuel incorrect.')
                    return redirect(reverse('dashboard:settings') + '?tab=account')
                from accounts.passwords import password_problem
                pw_error = password_problem(new_password, user.username, user.email)
                if pw_error:
                    django_messages.error(request, pw_error)
                    return redirect(reverse('dashboard:settings') + '?tab=account')
                if new_password != request.POST.get('confirm_password', ''):
                    django_messages.error(request, 'Les mots de passe ne correspondent pas.')
                    return redirect(reverse('dashboard:settings') + '?tab=account')
                user.set_password(new_password)
                user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, user)
                django_messages.success(request, 'Compte et mot de passe mis à jour.')
            else:
                django_messages.success(request, 'Informations du compte enregistrées.')

        tab = section if section in ('invoicing', 'payments', 'notifications', 'account') else 'invoicing'
        return redirect(reverse('dashboard:settings') + f'?tab={tab}')

    from messaging.models import NotificationPreference
    from billing.models import PaymentConfig
    from billing.payments import METHODS, config_methods
    notif_prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    pay_cfg = PaymentConfig.objects.filter(user=store.owner).first() or PaymentConfig(user=store.owner)
    active = config_methods(pay_cfg) if pay_cfg.pk else {}
    tab = request.GET.get('tab')

    return render(request, 'dashboard/settings.html', {
        'store': store,
        'invoice_settings': invoice_settings,
        'notif_prefs': notif_prefs,
        'pay_cfg': pay_cfg,
        'pay_active': [METHODS[c] for c in METHODS if c in active],
        'tab': tab if tab in ('invoicing', 'payments', 'notifications', 'account') else ('invoicing' if active else 'payments'),
    })


# ======== ADMIN USER MANAGEMENT ========
@login_required
def admin_users(request):
    if not request.user.is_superuser and request.user.role != 'admin':
        return redirect('dashboard:index')
    users = User.objects.all().annotate(
        order_count=Count('orders', distinct=True)
    ).order_by('-date_joined')
    role = request.GET.get('role')
    if role: users = users.filter(role=role)
    search = request.GET.get('q', '')
    if search: users = users.filter(
        Q(username__icontains=search) | Q(email__icontains=search) |
        Q(first_name__icontains=search) | Q(last_name__icontains=search)
    )
    return render(request, 'dashboard/users.html', {
        'users': users,
        'total': User.objects.count(),
        'buyers': User.objects.filter(role='buyer').count(),
        'sellers': User.objects.filter(role='seller').count(),
        'admins': User.objects.filter(role='admin').count(),
        'verified': User.objects.filter(is_verified=True).count(),
        'search': search, 'current_role': role,
    })


@login_required
def admin_user_detail(request, pk):
    if not request.user.is_superuser: return redirect('dashboard:index')
    target = get_object_or_404(User, pk=pk)
    orders = Order.objects.filter(buyer=target) if target.role == 'buyer' else Order.objects.none()
    store = getattr(target, 'store', None) if target.is_seller else None
    return render(request, 'dashboard/user_detail.html', {
        'target': target, 'orders': orders, 'store': store,
    })


@login_required
def admin_user_action(request, pk):
    if not request.user.is_superuser: return redirect('dashboard:index')
    target = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'verify':
            target.is_verified = True
            target.save()
            django_messages.success(request, f'{target.display_name} vérifié.')
        elif action == 'unverify':
            target.is_verified = False
            target.save()
        elif action == 'deactivate':
            target.is_active = False
            target.save()
            django_messages.warning(request, f'{target.display_name} désactivé.')
        elif action == 'activate':
            target.is_active = True
            target.save()
            django_messages.success(request, f'{target.display_name} réactivé.')
        elif action == 'make_admin':
            target.role = 'admin'
            target.is_staff = True
            target.save()
            django_messages.success(request, f'{target.display_name} est maintenant admin.')
        elif action == 'change_role':
            new_role = request.POST.get('new_role')
            if new_role: target.role = new_role; target.save()
    return redirect('dashboard:user_detail', pk=pk)


@login_required
def admin_stores(request):
    if not request.user.is_superuser: return redirect('dashboard:index')
    stores = Store.objects.annotate(
        product_cnt=Count('products'),
        revenue=Sum(F('products__orderitem__price') * F('products__orderitem__quantity'),
                     filter=Q(products__orderitem__order__is_paid=True))
    ).order_by('-created_at')
    return render(request, 'dashboard/stores.html', {'stores': stores})


@login_required
def admin_verify_store(request, pk):
    if not request.user.is_superuser: return redirect('dashboard:index')
    store = get_object_or_404(Store, pk=pk)
    store.is_verified = not store.is_verified
    store.save()
    status = 'vérifiée' if store.is_verified else 'non vérifiée'
    django_messages.success(request, f'Boutique {store.name} : {status}.')
    return redirect('dashboard:stores')


@login_required
def analytics(request):
    if not request.user.is_superuser: return redirect('dashboard:index')
    now = timezone.now()
    # Conversion funnel
    total_visits = Product.objects.aggregate(t=Sum('views_count'))['t'] or 1
    total_cart = Order.objects.count()
    total_paid = Order.objects.filter(is_paid=True).count()
    # Growth
    this_month = Order.objects.filter(created_at__month=now.month, created_at__year=now.year, is_paid=True).aggregate(t=Sum('total_amount'))['t'] or 0
    last_month = Order.objects.filter(created_at__month=(now.month - 1) or 12, is_paid=True).aggregate(t=Sum('total_amount'))['t'] or 1
    growth = round((this_month - last_month) / max(last_month, 1) * 100, 1)
    # Category performance
    cat_perf = Category.objects.filter(parent__isnull=True).annotate(
        rev=Sum(F('products__orderitem__price') * F('products__orderitem__quantity'),
                filter=Q(products__orderitem__order__is_paid=True)),
        cnt=Count('products__orderitem', filter=Q(products__orderitem__order__is_paid=True))
    ).order_by('-rev')[:8]
    cat_labels = [c.name for c in cat_perf]
    cat_data = [int(c.rev or 0) for c in cat_perf]
    return render(request, 'dashboard/analytics.html', {
        'total_visits': total_visits, 'total_cart': total_cart,
        'total_paid': total_paid, 'growth': growth,
        'this_month': this_month, 'last_month': last_month,
        'cat_labels': json.dumps(cat_labels), 'cat_data': json.dumps(cat_data),
    })


@login_required
def admin_users(request):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    users = User.objects.all().order_by('-date_joined')
    role = request.GET.get('role')
    if role:
        users = users.filter(role=role)
    search = request.GET.get('q', '')
    if search:
        users = users.filter(
            Q(username__icontains=search) | Q(email__icontains=search) |
            Q(first_name__icontains=search) | Q(last_name__icontains=search)
        )
    return render(request, 'dashboard/users.html', {
        'users': users, 'total': users.count(),
        'buyers': User.objects.filter(role='buyer').count(),
        'sellers_count': User.objects.filter(role='seller').count(),
        'admins': User.objects.filter(role='admin').count(),
    })


@login_required
def admin_user_detail(request, pk):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    u = get_object_or_404(User, pk=pk)
    orders_qs = Order.objects.filter(buyer=u) if u.role == 'buyer' else Order.objects.filter(items__store__owner=u).distinct()
    from accounting.models import Transaction, SellerWallet
    txns = Transaction.objects.filter(user=u)[:20]
    wallet = SellerWallet.objects.filter(user=u).first() if u.is_seller else None
    from billing.models import Subscription
    sub = Subscription.objects.filter(user=u).first()
    return render(request, 'dashboard/user_detail.html', {
        'profile_user': u, 'orders': orders_qs[:10],
        'transactions': txns, 'wallet': wallet, 'subscription': sub,
    })


@login_required
def admin_toggle_user(request, pk):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    u = get_object_or_404(User, pk=pk)
    u.is_active = not u.is_active
    u.save()
    status = 'activé' if u.is_active else 'désactivé'
    django_messages.success(request, f'Utilisateur {u.username} {status}.')
    return redirect('dashboard:users')


@login_required
def admin_sellers(request):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    sellers = Store.objects.select_related('owner').all()
    return render(request, 'dashboard/sellers.html', {'sellers': sellers})


@login_required
def admin_verify_seller(request, pk):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    store = get_object_or_404(Store, pk=pk)
    store.is_verified = not store.is_verified
    store.save()
    store.owner.is_verified = store.is_verified
    store.owner.save()
    status = 'vérifié' if store.is_verified else 'non vérifié'
    django_messages.success(request, f'{store.name} est maintenant {status}.')
    return redirect('dashboard:sellers')


@login_required
def admin_payouts(request):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    from accounting.models import PayoutRequest
    payouts = PayoutRequest.objects.select_related('user').all()
    status = request.GET.get('status')
    if status:
        payouts = payouts.filter(status=status)
    return render(request, 'dashboard/payouts.html', {'payouts': payouts})


@login_required
def admin_process_payout(request, pk):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    from accounting.models import PayoutRequest, SellerWallet, Transaction
    payout = get_object_or_404(PayoutRequest, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            payout.status = 'completed'
            payout.processed_at = timezone.now()
            payout.save()
            wallet = SellerWallet.objects.get(user=payout.user)
            wallet.pending_balance -= payout.amount
            wallet.total_withdrawn += payout.amount
            wallet.save()
            Transaction.objects.create(
                user=payout.user, type='payout', amount=payout.amount,
                status='completed', reference=f'Versement #{payout.pk}',
            )
            django_messages.success(request, f'Versement de {payout.amount:,.0f} F approuvé.')
        elif action == 'reject':
            payout.status = 'rejected'
            payout.admin_notes = request.POST.get('notes', '')
            payout.save()
            wallet = SellerWallet.objects.get(user=payout.user)
            wallet.pending_balance -= payout.amount
            wallet.balance += payout.amount
            wallet.save()
            django_messages.info(request, 'Versement rejeté.')
    return redirect('dashboard:payouts')


@login_required
def admin_platform_stats(request):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    from accounting.models import Transaction, Expense
    from billing.models import Subscription, ProductBoost

    total_users = User.objects.count()
    active_subscriptions = Subscription.objects.filter(status='active').count()
    total_sub_revenue = Transaction.objects.filter(type='subscription', status='completed').aggregate(t=Sum('amount'))['t'] or 0
    total_boost_revenue = Transaction.objects.filter(type='boost', status='completed').aggregate(t=Sum('amount'))['t'] or 0
    total_commissions = Transaction.objects.filter(type='commission', status='completed').aggregate(t=Sum('amount'))['t'] or 0
    total_expenses = Expense.objects.aggregate(t=Sum('amount'))['t'] or 0
    total_orders = Order.objects.count()
    gmv = Order.objects.filter(is_paid=True).aggregate(t=Sum('total_amount'))['t'] or 0

    return render(request, 'dashboard/platform_stats.html', {
        'total_users': total_users,
        'active_subscriptions': active_subscriptions,
        'total_sub_revenue': total_sub_revenue,
        'total_boost_revenue': total_boost_revenue,
        'total_commissions': total_commissions,
        'total_expenses': total_expenses,
        'total_orders': total_orders,
        'gmv': gmv,
        'net_revenue': (total_sub_revenue or 0) + (total_boost_revenue or 0) + (total_commissions or 0) - (total_expenses or 0),
    })


@login_required
def admin_banners(request):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    from catalog.models import HeroBanner
    banners = HeroBanner.objects.all()
    return render(request, 'dashboard/banners.html', {'banners': banners})


@login_required
def admin_banner_edit(request, pk=None):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    from catalog.models import HeroBanner
    banner = get_object_or_404(HeroBanner, pk=pk) if pk else None
    if request.method == 'POST':
        data = {
            'title': request.POST.get('title', ''),
            'subtitle': request.POST.get('subtitle', ''),
            'button_text': request.POST.get('button_text', ''),
            'button_url': request.POST.get('button_url', ''),
            'button2_text': request.POST.get('button2_text', ''),
            'button2_url': request.POST.get('button2_url', ''),
            'order': int(request.POST.get('order', 0)),
            'is_active': 'is_active' in request.POST,
        }
        if banner:
            for k, v in data.items():
                setattr(banner, k, v)
            if request.FILES.get('image'):
                banner.image = request.FILES['image']
            banner.save()
        else:
            banner = HeroBanner(**data)
            if request.FILES.get('image'):
                banner.image = request.FILES['image']
            banner.save()
        django_messages.success(request, 'Bannière sauvegardée !')
        return redirect('dashboard:banners')
    return render(request, 'dashboard/banner_edit.html', {'banner': banner})


@login_required
def admin_banner_delete(request, pk):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')
    from catalog.models import HeroBanner
    banner = get_object_or_404(HeroBanner, pk=pk)
    if request.method == 'POST':
        banner.delete()
        django_messages.success(request, 'Bannière supprimée.')
    return redirect('dashboard:banners')

@login_required
def categories(request):
    """Gestion des catégories"""
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')

    categories = Category.objects.all().order_by('order', 'name')
    return render(request, 'dashboard/categories.html', {'categories': categories})

@login_required
def category_add(request):
    """Ajouter une catégorie"""
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')

    if request.method == 'POST':
        name = request.POST.get('name')
        icon = request.POST.get('icon', '📦')
        parent_id = request.POST.get('parent')
        order = request.POST.get('order', 0)
        description = request.POST.get('description', '')

        if name:
            slug = slugify(name)
            parent = Category.objects.get(pk=parent_id) if parent_id else None

            Category.objects.create(
                name=name,
                slug=slug,
                icon=icon,
                parent=parent,
                order=order,
                description=description
            )
            django_messages.success(request, f'Catégorie "{name}" créée avec succès!')
            return redirect('dashboard:categories')

    parent_categories = Category.objects.filter(parent__isnull=True)
    return render(request, 'dashboard/category_form.html', {
        'parent_categories': parent_categories,
        'title': 'Ajouter une catégorie'
    })

@login_required
def category_edit(request, pk):
    """Modifier une catégorie"""
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')

    category = get_object_or_404(Category, pk=pk)

    if request.method == 'POST':
        category.name = request.POST.get('name')
        category.slug = slugify(category.name)
        category.icon = request.POST.get('icon', '📦')
        category.description = request.POST.get('description', '')
        category.order = request.POST.get('order', 0)

        parent_id = request.POST.get('parent')
        category.parent = Category.objects.get(pk=parent_id) if parent_id else None

        category.save()
        django_messages.success(request, f'Catégorie "{category.name}" modifiée!')
        return redirect('dashboard:categories')

    parent_categories = Category.objects.filter(parent__isnull=True).exclude(pk=category.pk)
    return render(request, 'dashboard/category_form.html', {
        'category': category,
        'parent_categories': parent_categories,
        'title': 'Modifier la catégorie'
    })

@login_required
def category_delete(request, pk):
    """Supprimer une catégorie"""
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return redirect('dashboard:index')

    category = get_object_or_404(Category, pk=pk)

    if request.method == 'POST':
        name = category.name
        category.delete()
        django_messages.success(request, f'Catégorie "{name}" supprimée!')
        return redirect('dashboard:categories')

    return render(request, 'dashboard/category_delete.html', {'category': category})
