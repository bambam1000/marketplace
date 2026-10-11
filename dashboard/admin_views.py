"""Espace administrateur de la plateforme : vue d'ensemble, commandes, boutiques, produits, avis,
messages de contact, revenus et abonnements. Réservé aux administrateurs (store.access.is_admin)."""
from collections import OrderedDict
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDay, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from catalog.models import Category, ContactMessage, Product, Review
from orders.models import Order, OrderItem
from store import access
from store.models import Store

VALID = ~Q(status__in=('cancelled', 'refunded'))
PERIODS = OrderedDict([('7', 7), ('30', 30), ('90', 90), ('365', 365)])


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not access.is_admin(request.user):
            messages.error(request, "Cette page est réservée à l'administration de la plateforme.")
            return redirect('dashboard:index')
        return view(request, *args, **kwargs)
    return wrapper


def _page(request, qs, per=25):
    return Paginator(qs, per).get_page(request.GET.get('page'))


def _back(request, fallback):
    nxt = request.POST.get('next', '')
    return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else fallback)


def _money(qs, field):
    return qs.aggregate(t=Coalesce(Sum(field), Value(0), output_field=DecimalField()))['t'] or 0


def _evolution(cur, prev):
    if not prev:
        return None
    return round((float(cur) - float(prev)) / float(prev) * 100)


def platform_revenue(start, end):
    """Ce que gagne la plateforme : commissions sur les ventes en ligne, abonnements, boosts."""
    from accounting.models import Transaction
    tx = Transaction.objects.filter(status='completed', created_at__gte=start, created_at__lt=end)
    commissions = _money(tx.filter(type='sale'), 'commission_amount')
    subs = _money(tx.filter(type='subscription'), 'amount')
    boosts = _money(tx.filter(type='boost'), 'amount')
    return {'commissions': commissions, 'subscriptions': subs, 'boosts': boosts, 'total': commissions + subs + boosts}


# ───────────────────────── Vue d'ensemble ─────────────────────────

@admin_required
def home(request):
    key = request.GET.get('p', '30')
    days = PERIODS.get(key, 30)
    key = str(days)
    now = timezone.now()
    start, prev_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    orders = Order.objects.filter(VALID)
    cur, prev = orders.filter(created_at__gte=start), orders.filter(created_at__gte=prev_start, created_at__lt=start)
    gmv, gmv_prev = _money(cur, 'total_amount'), _money(prev, 'total_amount')
    rev, rev_prev = platform_revenue(start, now), platform_revenue(prev_start, start)
    users_new = User.objects.filter(date_joined__gte=start)
    users_prev = User.objects.filter(date_joined__gte=prev_start, date_joined__lt=start).count()
    stores_new = Store.objects.filter(created_at__gte=start).count()

    kpis = [
        {'label': "Volume d'affaires", 'icon': 'fa-sack-dollar', 'value': gmv, 'money': True, 'evo': _evolution(gmv, gmv_prev),
         'sub': f'{cur.count()} commandes'},
        {'label': 'Revenus de la plateforme', 'icon': 'fa-coins', 'value': rev['total'], 'money': True,
         'evo': _evolution(rev['total'], rev_prev['total']), 'sub': 'Commissions, abonnements, boosts', 'url': reverse('dashboard:platform_stats')},
        {'label': 'Nouveaux inscrits', 'icon': 'fa-user-plus', 'value': users_new.count(), 'evo': _evolution(users_new.count(), users_prev),
         'sub': f"{users_new.filter(role='seller').count()} vendeurs · {users_new.filter(role='buyer').count()} acheteurs", 'url': reverse('dashboard:users')},
        {'label': 'Boutiques', 'icon': 'fa-store', 'value': Store.objects.filter(is_active=True).count(),
         'sub': f'{stores_new} nouvelle{"s" if stores_new > 1 else ""} sur la période', 'url': reverse('dashboard:sellers')},
    ]

    # Courbe : volume d'affaires et commandes par jour (par mois sur 12 mois)
    trunc = TruncMonth if days > 90 else TruncDay
    series = {r['d'].date() if hasattr(r['d'], 'date') else r['d']: r for r in
              cur.annotate(d=trunc('created_at')).values('d').annotate(gmv=Sum('total_amount'), n=Count('id'))}
    labels, gmv_s, n_s = [], [], []
    if days > 90:
        y, m = now.year, now.month
        months = []
        for _ in range(12):
            months.append((y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        for y, m in reversed(months):
            row = next((r for d, r in series.items() if d.year == y and d.month == m), None)
            labels.append(f'{m:02d}/{str(y)[2:]}')
            gmv_s.append(int(row['gmv'] or 0) if row else 0)
            n_s.append(row['n'] if row else 0)
    else:
        for i in range(days - 1, -1, -1):
            d = (now - timedelta(days=i)).date()
            row = series.get(d)
            labels.append(d.strftime('%d/%m'))
            gmv_s.append(int(row['gmv'] or 0) if row else 0)
            n_s.append(row['n'] if row else 0)

    from accounting.models import PayoutRequest
    pending_payouts = PayoutRequest.objects.filter(status='pending')
    stale = Order.objects.filter(status='pending', created_at__lt=now - timedelta(days=3))
    todo = [
        ('fa-store', 'Boutiques à vérifier', Store.objects.filter(is_active=True, is_verified=False).count(),
         reverse('dashboard:sellers') + '?f=to_verify', 'Vérifiez leur identité avant de leur donner le badge « Vérifié »'),
        ('fa-money-bill-transfer', 'Versements à traiter', pending_payouts.count(), reverse('dashboard:payouts') + '?status=pending',
         f"{_money(pending_payouts, 'amount'):,.0f} F demandés par les vendeurs".replace(',', ' ')),
        ('fa-envelope', 'Messages de contact non lus', ContactMessage.objects.filter(is_read=False).count(),
         reverse('dashboard:admin_messages') + '?f=unread', 'Questions envoyées depuis la page Contact'),
        ('fa-hourglass-half', 'Commandes en attente depuis plus de 3 jours', stale.count(),
         reverse('dashboard:admin_orders') + '?status=pending&stale=1', 'La boutique ne les a pas confirmées'),
        ('fa-star-half-stroke', 'Avis négatifs récents', Review.objects.filter(rating__lte=2, created_at__gte=now - timedelta(days=14)).count(),
         reverse('dashboard:admin_reviews') + '?rating=low', 'Notes de 1 ou 2 étoiles sur les 14 derniers jours'),
    ]

    items = OrderItem.objects.filter(order__in=cur)
    top_stores = (items.values('store__name', 'store__slug').annotate(gmv=Sum(F('price') * F('quantity')), n=Count('order', distinct=True))
                  .order_by('-gmv')[:5])
    top_categories = (items.values('product__category__name').annotate(gmv=Sum(F('price') * F('quantity')))
                      .order_by('-gmv')[:5])
    recent = list(Order.objects.select_related('buyer').prefetch_related('items__store')[:6])
    for o in recent:
        o.stores = list(dict.fromkeys(i.store.name for i in o.items.all()))
    return render(request, 'dashboard/admin/home.html', {
        'period': key, 'periods': [(k, {'7': '7 jours', '30': '30 jours', '90': '90 jours', '365': '12 mois'}[k]) for k in PERIODS],
        'kpis': kpis, 'chart': {'labels': labels, 'gmv': gmv_s, 'orders': n_s},
        'todo': todo, 'todo_count': sum(1 for t in todo if t[2]),
        'revenue': rev, 'top_stores': top_stores, 'top_categories': top_categories,
        'recent_orders': recent,
        'recent_users': User.objects.order_by('-date_joined')[:6],
    })


# ───────────────────────── Commandes ─────────────────────────

@admin_required
def orders(request):
    qs = Order.objects.select_related('buyer').prefetch_related('items__store').exclude(shipping_address=Order.DIRECT_SALE_ADDRESS)
    counts = {s: n for s, n in qs.values_list('status').annotate(n=Count('id'))}
    tabs = [('', 'Toutes', sum(counts.values()))] + [(c, l, counts.get(c, 0)) for c, l in Order.STATUS_CHOICES]
    status = request.GET.get('status', '')
    if status in dict(Order.STATUS_CHOICES):
        qs = qs.filter(status=status)
    else:
        status = ''
    if request.GET.get('stale'):
        qs = qs.filter(created_at__lt=timezone.now() - timedelta(days=3))
    paid = request.GET.get('paid')
    if paid in ('1', '0'):
        qs = qs.filter(is_paid=paid == '1')
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(order_number__icontains=q) | Q(buyer__username__icontains=q) | Q(buyer__email__icontains=q)
                       | Q(shipping_name__icontains=q) | Q(items__store__name__icontains=q)).distinct()
    page = _page(request, qs)
    for o in page:
        o.stores = list(dict.fromkeys(i.store.name for i in o.items.all()))
    return render(request, 'dashboard/admin/orders.html', {'page': page, 'tabs': tabs, 'status': status, 'q': q, 'paid': paid,
                                                           'gmv': _money(qs.filter(VALID), 'total_amount')})


# ───────────────────────── Boutiques ─────────────────────────

@admin_required
def stores(request):
    # Sous-requêtes séparées : une jointure commune multiplierait les ventes par le nombre de produits
    from django.db.models import IntegerField, OuterRef, Subquery
    lines = OrderItem.objects.filter(store=OuterRef('pk')).values('store')
    sold = lines.filter(order__is_paid=True).exclude(order__status__in=('cancelled', 'refunded'))
    qs = (Store.objects.select_related('owner', 'owner__subscription__plan')
          .annotate(n_products=Coalesce(Subquery(Product.objects.filter(store=OuterRef('pk'), is_active=True).values('store')
                                                 .annotate(n=Count('id')).values('n')[:1], output_field=IntegerField()), Value(0)),
                    n_orders=Coalesce(Subquery(lines.annotate(n=Count('order', distinct=True)).values('n')[:1], output_field=IntegerField()), Value(0)),
                    sales=Coalesce(Subquery(sold.annotate(t=Sum(F('price') * F('quantity'))).values('t')[:1], output_field=DecimalField()),
                                   Value(0), output_field=DecimalField()))
          .order_by('-created_at'))
    counts = {
        '': Store.objects.count(),
        'to_verify': Store.objects.filter(is_active=True, is_verified=False).count(),
        'verified': Store.objects.filter(is_verified=True).count(),
        'suspended': Store.objects.filter(is_active=False).count(),
    }
    f = request.GET.get('f', '')
    filters = {'to_verify': Q(is_active=True, is_verified=False), 'verified': Q(is_verified=True), 'suspended': Q(is_active=False)}
    if f in filters:
        qs = qs.filter(filters[f])
    else:
        f = ''
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(owner__username__icontains=q) | Q(owner__email__icontains=q) | Q(city__icontains=q))
    tabs = [('', 'Toutes'), ('to_verify', 'À vérifier'), ('verified', 'Vérifiées'), ('suspended', 'Suspendues')]
    return render(request, 'dashboard/admin/stores.html', {
        'page': _page(request, qs, 20), 'tabs': [(k, l, counts[k]) for k, l in tabs], 'f': f, 'q': q})


@admin_required
def store_action(request, pk):
    if request.method != 'POST':
        return redirect('dashboard:sellers')
    from messaging.utils import notify
    store = get_object_or_404(Store.objects.select_related('owner'), pk=pk)
    action, reason = request.POST.get('action'), (request.POST.get('reason') or '').strip()[:300]
    if action == 'verify':
        store.is_verified = True
        store.owner.is_verified = True
        store.owner.save(update_fields=['is_verified'])
        notify(store.owner, 'account', 'Boutique vérifiée', f'« {store.name} » affiche désormais le badge « Vérifié ».', url='/dashboard/', send_email=True)
        msg = f'« {store.name} » est vérifiée.'
    elif action == 'unverify':
        store.is_verified = False
        msg = f'Badge « Vérifié » retiré à « {store.name} ».'
    elif action == 'suspend':
        store.is_active = False
        notify(store.owner, 'account', 'Boutique suspendue',
               f'« {store.name} » et ses produits ne sont plus visibles sur Comptoir.' + (f' Motif : {reason}' if reason else '')
               + ' Contactez-nous pour en savoir plus.', url='/contact/', send_email=True)
        msg = f'« {store.name} » est suspendue : elle et ses produits sont masqués du site.'
    elif action == 'activate':
        store.is_active = True
        notify(store.owner, 'account', 'Boutique réactivée', f'« {store.name} » est de nouveau visible sur Comptoir.', url='/dashboard/', send_email=True)
        msg = f'« {store.name} » est réactivée.'
    else:
        return redirect('dashboard:sellers')
    store.save(update_fields=['is_verified', 'is_active'])
    messages.success(request, msg)
    return _back(request, reverse('dashboard:sellers'))


# ───────────────────────── Produits ─────────────────────────

@admin_required
def products(request):
    qs = Product.objects.select_related('store', 'category').order_by('-created_at')
    filters = {'active': Q(is_active=True), 'inactive': Q(is_active=False), 'featured': Q(is_featured=True),
               'out': Q(is_active=True, stock=0), 'suspended_store': Q(store__is_active=False)}
    counts = {k: Product.objects.filter(v).count() for k, v in filters.items()}
    counts[''] = Product.objects.count()
    f = request.GET.get('f', '')
    if f in filters:
        qs = qs.filter(filters[f])
    else:
        f = ''
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(store__name__icontains=q) | Q(sku__icontains=q))
    cat = request.GET.get('category', '')
    if cat.isdigit():
        qs = qs.filter(Q(category_id=cat) | Q(category__parent_id=cat))
    tabs = [('', 'Tous'), ('active', 'En ligne'), ('inactive', 'Désactivés'), ('featured', 'À la une'), ('out', 'En rupture'),
            ('suspended_store', 'Boutique suspendue')]
    return render(request, 'dashboard/admin/products.html', {
        'page': _page(request, qs, 24), 'tabs': [(k, l, counts[k]) for k, l in tabs], 'f': f, 'q': q, 'cat': cat,
        'categories': Category.objects.filter(parent__isnull=True).order_by('name')})


@admin_required
def product_action(request, pk):
    if request.method != 'POST':
        return redirect('dashboard:admin_products')
    from messaging.utils import notify_store
    p = get_object_or_404(Product.objects.select_related('store'), pk=pk)
    action, reason = request.POST.get('action'), (request.POST.get('reason') or '').strip()[:300]
    if action == 'deactivate':
        p.is_active = False
        notify_store(p.store, 'products.view', 'system', 'Produit retiré par Comptoir',
                     f'« {p.name} » n\'est plus visible sur le site.' + (f' Motif : {reason}' if reason else ''),
                     url=f'/dashboard/produits/{p.pk}/modifier/', send_email=True)
        msg = f'« {p.name} » est retiré du site.'
    elif action == 'activate':
        p.is_active = True
        msg = f'« {p.name} » est de nouveau en ligne.'
    elif action == 'feature':
        p.is_featured = True
        msg = f'« {p.name} » est mis à la une.'
    elif action == 'unfeature':
        p.is_featured = False
        msg = f'« {p.name} » n\'est plus à la une.'
    else:
        return redirect('dashboard:admin_products')
    p.save(update_fields=['is_active', 'is_featured'])
    messages.success(request, msg)
    return _back(request, reverse('dashboard:admin_products'))


# ───────────────────────── Avis ─────────────────────────

@admin_required
def reviews(request):
    qs = Review.objects.select_related('product', 'product__store', 'user').order_by('-created_at')
    rating = request.GET.get('rating', '')
    if rating == 'low':
        qs = qs.filter(rating__lte=2)
    elif rating.isdigit():
        qs = qs.filter(rating=int(rating))
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(comment__icontains=q) | Q(name__icontains=q) | Q(product__name__icontains=q))
    if request.method == 'POST' and request.POST.get('action') == 'delete':
        r = get_object_or_404(Review, pk=request.POST.get('review'))
        r.delete()
        messages.success(request, 'Avis supprimé.')
        return _back(request, reverse('dashboard:admin_reviews'))
    dist = dict(Review.objects.values_list('rating').annotate(n=Count('id')))
    return render(request, 'dashboard/admin/reviews.html', {
        'page': _page(request, qs, 20), 'rating': rating, 'q': q,
        'dist': [(n, dist.get(n, 0)) for n in range(5, 0, -1)], 'total': sum(dist.values())})


# ───────────────────────── Messages de contact ─────────────────────────

@admin_required
def contact_messages(request):
    if request.method == 'POST':
        m = get_object_or_404(ContactMessage, pk=request.POST.get('message'))
        action = request.POST.get('action')
        if action == 'delete':
            m.delete()
            messages.success(request, 'Message supprimé.')
        elif action in ('read', 'unread'):
            m.is_read = action == 'read'
            m.save(update_fields=['is_read'])
        return _back(request, reverse('dashboard:admin_messages'))
    qs = ContactMessage.objects.all()
    f = request.GET.get('f', '')
    if f == 'unread':
        qs = qs.filter(is_read=False)
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(subject__icontains=q) | Q(message__icontains=q))
    return render(request, 'dashboard/admin/messages.html', {
        'page': _page(request, qs, 20), 'f': f, 'q': q,
        'unread': ContactMessage.objects.filter(is_read=False).count(), 'total': ContactMessage.objects.count()})


# ───────────────────────── Revenus et abonnements ─────────────────────────

@admin_required
def revenue(request):
    from accounting.models import Expense, PayoutRequest, SellerWallet, Transaction
    from billing.models import Subscription
    now = timezone.now()
    tx = Transaction.objects.filter(status='completed')
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    for i in range(11, -1, -1):
        y, m = first.year, first.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        start = first.replace(year=y, month=m)
        end = start.replace(year=y + (m == 12), month=1 if m == 12 else m + 1)
        months.append((start, end))
    rows = []
    for start, end in months:
        r = platform_revenue(start, end)
        r['label'] = start.strftime('%m/%Y')
        r['gmv'] = _money(Order.objects.filter(VALID, created_at__gte=start, created_at__lt=end), 'total_amount')
        rows.append(r)
    year_start = months[0][0]
    totals = platform_revenue(year_start, now + timedelta(days=1))
    expenses = _money(Expense.objects.filter(date__gte=year_start.date()), 'amount')
    top = (tx.filter(type='sale', created_at__gte=year_start).values('user__username', 'user__first_name', 'user__last_name')
           .annotate(sales=Sum('amount'), commission=Sum('commission_amount')).order_by('-commission')[:8])
    return render(request, 'dashboard/admin/revenue.html', {
        'rows': list(reversed(rows)), 'chart': {'labels': [r['label'] for r in rows],
                                                'commissions': [int(r['commissions']) for r in rows],
                                                'subscriptions': [int(r['subscriptions']) for r in rows],
                                                'boosts': [int(r['boosts']) for r in rows]},
        'totals': totals, 'expenses': expenses, 'net': totals['total'] - expenses, 'top': top,
        'wallets': _money(SellerWallet.objects.all(), 'balance'),
        'pending_payouts': _money(PayoutRequest.objects.filter(status='pending'), 'amount'),
        'active_subs': Subscription.objects.filter(status='active', end_date__gte=now).count(),
    })


@admin_required
def subscriptions(request):
    from billing.models import Subscription, SubscriptionPlan
    now = timezone.now()
    qs = Subscription.objects.select_related('user', 'plan').order_by('end_date')
    overdue = Q(status='active', end_date__lt=now)  # marqué actif mais échéance dépassée
    status = request.GET.get('status', '')
    if status == 'overdue':
        qs = qs.filter(overdue)
    elif status == 'active':
        qs = qs.filter(status='active', end_date__gte=now)
    elif status in dict(Subscription.STATUS_CHOICES):
        qs = qs.filter(status=status)
    else:
        status = ''
    plan = request.GET.get('plan', '')
    if plan.isdigit():
        qs = qs.filter(plan_id=plan)
    soon = now + timedelta(days=7)
    live = Q(subscription__status='active', subscription__end_date__gte=now)
    plans = SubscriptionPlan.objects.annotate(n=Count('subscription', filter=live))
    counts = dict(Subscription.objects.values_list('status').annotate(n=Count('id')))
    n_overdue = Subscription.objects.filter(overdue).count()
    counts['active'] = counts.get('active', 0) - n_overdue
    tabs = [('', 'Tous', sum(counts.values()) + n_overdue), ('active', 'En cours', counts['active']),
            ('overdue', 'Échus, à relancer', n_overdue)] + [(c, l, counts.get(c, 0)) for c, l in Subscription.STATUS_CHOICES if c != 'active']
    return render(request, 'dashboard/admin/subscriptions.html', {
        'page': _page(request, qs, 25), 'status': status, 'plan': plan, 'plans': plans, 'soon': soon, 'now': now,
        'n_overdue': n_overdue, 'tabs': tabs,
        'mrr': sum((p.price_monthly or 0) * p.n for p in plans),
    })


# ───────────────────────── Utilisateurs ─────────────────────────

ROLE_FILTERS = {'buyer': Q(role='buyer'), 'seller': Q(role='seller'), 'admin': Q(role='admin') | Q(is_superuser=True),
                'inactive': Q(is_active=False)}


@admin_required
def users(request):
    qs = User.objects.annotate(n_orders=Count('orders', distinct=True)).order_by('-date_joined')
    counts = {k: User.objects.filter(v).count() for k, v in ROLE_FILTERS.items()}
    counts[''] = User.objects.count()
    role = request.GET.get('role', '')
    if role in ROLE_FILTERS:
        qs = qs.filter(ROLE_FILTERS[role])
    else:
        role = ''
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                       | Q(last_name__icontains=q) | Q(phone__icontains=q))
    tabs = [('', 'Tous'), ('buyer', 'Acheteurs'), ('seller', 'Vendeurs'), ('admin', 'Administrateurs'), ('inactive', 'Désactivés')]
    week = timezone.now() - timedelta(days=7)
    return render(request, 'dashboard/admin/users.html', {
        'page': _page(request, qs, 25), 'role': role, 'q': q, 'tabs': [(k, l, counts[k]) for k, l in tabs],
        'new_week': User.objects.filter(date_joined__gte=week).count(),
        'active_month': User.objects.filter(last_login__gte=timezone.now() - timedelta(days=30)).count(),
    })


@admin_required
def user_detail(request, pk):
    from accounting.models import SellerWallet, Transaction
    from billing.models import Subscription
    u = get_object_or_404(User, pk=pk)
    orders_qs = Order.objects.filter(buyer=u).exclude(shipping_address=Order.DIRECT_SALE_ADDRESS)
    sales = OrderItem.objects.filter(store__owner=u)
    return render(request, 'dashboard/admin/user_detail.html', {
        'u': u, 'stores': u.stores.all(),
        'orders': orders_qs.prefetch_related('items__store')[:8],
        'stats': {
            'orders': orders_qs.count(),
            'spent': _money(orders_qs.filter(VALID, is_paid=True), 'total_amount'),
            'sales_orders': sales.values('order').distinct().count(),
            'sales': sales.filter(order__is_paid=True).exclude(order__status__in=('cancelled', 'refunded'))
                          .aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0,
        },
        'wallet': SellerWallet.objects.filter(user=u).first(),
        'subscription': Subscription.objects.filter(user=u).select_related('plan').first(),
        'transactions': Transaction.objects.filter(user=u).order_by('-created_at')[:8],
        'memberships': u.store_memberships.select_related('store', 'role'),
        'is_self': u.pk == request.user.pk,
        'roles': User.ROLE_CHOICES,
    })


@admin_required
def user_action(request, pk):
    """Actions sur un compte (POST uniquement). On ne peut pas modifier son propre compte ici,
    ni un superutilisateur sans l'être soi-même."""
    u = get_object_or_404(User, pk=pk)
    if request.method != 'POST':
        return redirect('dashboard:user_detail', pk=pk)
    back = _back(request, reverse('dashboard:user_detail', args=[pk]))
    if u.pk == request.user.pk:
        messages.error(request, 'Vous ne pouvez pas modifier votre propre compte depuis cette page.')
        return back
    if u.is_superuser and not request.user.is_superuser:
        messages.error(request, 'Seul un superutilisateur peut modifier un autre superutilisateur.')
        return back
    action = request.POST.get('action')
    if action == 'deactivate':
        u.is_active = False
        u.save(update_fields=['is_active'])
        messages.warning(request, f'{u.display_name} est désactivé : il ne peut plus se connecter.')
    elif action == 'activate':
        u.is_active = True
        u.save(update_fields=['is_active'])
        messages.success(request, f'{u.display_name} est réactivé.')
    elif action in ('verify', 'unverify'):
        u.is_verified = action == 'verify'
        u.save(update_fields=['is_verified'])
        messages.success(request, f'{u.display_name} : compte {"vérifié" if u.is_verified else "non vérifié"}.')
    elif action == 'set_role':
        new = request.POST.get('role')
        if new not in dict(User.ROLE_CHOICES):
            return back
        if new == 'seller' and not u.stores.exists():
            messages.error(request, "Ce compte n'a pas de boutique : il doit en créer une pour devenir vendeur.")
            return back
        u.role = new
        u.is_staff = new == 'admin' or u.is_superuser
        u.save(update_fields=['role', 'is_staff'])
        messages.success(request, f'{u.display_name} est maintenant {u.get_role_display().lower()}.')
    return back


# ───────────────────────── Catégories ─────────────────────────

CATEGORY_ICONS = [
    'fa-shirt', 'fa-person-dress', 'fa-shoe-prints', 'fa-gem', 'fa-mobile-screen', 'fa-laptop', 'fa-tv', 'fa-headphones',
    'fa-camera', 'fa-gamepad', 'fa-couch', 'fa-kitchen-set', 'fa-blender', 'fa-house', 'fa-spray-can-sparkles', 'fa-pump-soap',
    'fa-baby', 'fa-puzzle-piece', 'fa-futbol', 'fa-dumbbell', 'fa-bicycle', 'fa-car', 'fa-screwdriver-wrench', 'fa-seedling',
    'fa-apple-whole', 'fa-wheat-awn', 'fa-bottle-water', 'fa-pills', 'fa-book', 'fa-pen-ruler', 'fa-briefcase', 'fa-paw',
    'fa-gift', 'fa-tag', 'fa-box', 'fa-store',
]


def _unique_slug(name, exclude_pk=None):
    from django.utils.text import slugify
    base = slugify(name)[:180] or 'categorie'
    slug, n = base, 2
    while Category.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug, n = f'{base}-{n}', n + 1
    return slug


@admin_required
def categories(request):
    from django.db.models import IntegerField, OuterRef, Subquery
    if request.method == 'POST' and request.POST.get('action') == 'purge_empty':
        # Seulement les catégories sans aucun produit ni sous-catégorie
        empty = Category.objects.filter(products__isnull=True, children__isnull=True)
        n = empty.count()
        empty.delete()
        messages.success(request, f'{n} catégorie(s) vide(s) supprimée(s).')
        return redirect('dashboard:categories')
    direct = Product.objects.filter(category=OuterRef('pk')).values('category').annotate(n=Count('id')).values('n')[:1]
    cats = list(Category.objects.annotate(n_direct=Coalesce(Subquery(direct, output_field=IntegerField()), Value(0)),
                                          n_children=Count('children', distinct=True)).order_by('order', 'name'))
    by_parent = {}
    for c in cats:
        by_parent.setdefault(c.parent_id, []).append(c)
    rows = []
    for parent in by_parent.get(None, []):
        kids = by_parent.get(parent.pk, [])
        parent.n_total = parent.n_direct + sum(k.n_direct for k in kids)
        rows.append((parent, kids))
    q = (request.GET.get('q') or '').strip().lower()
    f = request.GET.get('f', '')
    keep = {'empty': lambda c: not c.n_direct and not c.n_children, 'hidden': lambda c: not c.is_active}.get(f)
    if keep is None:
        f = ''
    match = lambda c: (not q or q in c.name.lower()) and (keep is None or keep(c))  # noqa: E731
    if q or keep:
        rows = [(p, kids if match(p) else [k for k in kids if match(k)]) for p, kids in rows if match(p) or any(match(k) for k in kids)]
    return render(request, 'dashboard/admin/categories.html', {
        'rows': rows, 'q': q, 'f': f, 'total': len(cats), 'parents': len(by_parent.get(None, [])),
        'empty': sum(1 for c in cats if not c.n_direct and not c.n_children),
        'inactive': sum(1 for c in cats if not c.is_active),
    })


@admin_required
def category_form(request, pk=None):
    category = get_object_or_404(Category, pk=pk) if pk else None
    errors, data = {}, {}
    if request.method == 'POST':
        data = {k: (request.POST.get(k) or '').strip() for k in ('name', 'icon', 'description', 'order', 'parent')}
        if not data['name']:
            errors['name'] = 'Le nom est obligatoire.'
        parent = None
        if data['parent']:
            parent = Category.objects.filter(pk=data['parent'] if data['parent'].isdigit() else 0, parent__isnull=True).first()
            if parent is None or (category and parent.pk == category.pk):
                errors['parent'] = 'Catégorie parente invalide.'
            elif category and category.children.exists():
                errors['parent'] = 'Cette catégorie a des sous-catégories : elle doit rester une catégorie principale.'
        if not errors:
            c = category or Category()
            c.name = data['name'][:200]
            c.slug = _unique_slug(c.name, exclude_pk=c.pk)
            c.icon = data['icon'] if data['icon'] in CATEGORY_ICONS else (c.icon if category else 'fa-tag')
            c.description = data['description']
            c.order = int(data['order']) if data['order'].lstrip('-').isdigit() else 0
            c.parent = parent
            c.is_active = 'is_active' in request.POST
            if request.FILES.get('image'):
                c.image = request.FILES['image']
            c.save()
            messages.success(request, f'Catégorie « {c.name} » {"modifiée" if category else "créée"}.')
            return redirect('dashboard:categories')
        active = 'is_active' in request.POST
    else:
        active = category.is_active if category else True
        if category:
            data = {'name': category.name, 'icon': category.icon, 'description': category.description,
                    'order': str(category.order), 'parent': str(category.parent_id or '')}
    return render(request, 'dashboard/admin/category_form.html', {
        'category': category, 'data': data, 'errors': errors, 'icons': CATEGORY_ICONS, 'is_active': active,
        'parents': Category.objects.filter(parent__isnull=True).exclude(pk=getattr(category, 'pk', None)).order_by('name'),
    }, status=400 if errors else 200)


@admin_required
def category_delete(request, pk):
    """Suppression sûre : produits et sous-catégories sont d'abord déplacés (sinon ils seraient supprimés avec elle)."""
    from django.db import transaction
    category = get_object_or_404(Category, pk=pk)
    products = Product.objects.filter(category=category)
    children = category.children.all()
    targets = Category.objects.exclude(pk=category.pk).exclude(parent=category).select_related('parent').order_by('name')
    if request.method == 'POST':
        target = None
        moved = products.count()
        if moved or children.exists():
            raw = request.POST.get('move_to', '')
            target = targets.filter(pk=raw if raw.isdigit() else 0).first()
            if target is None:
                messages.error(request, 'Choisissez la catégorie qui recevra les produits.')
                return redirect('dashboard:category_delete', pk=pk)
        name = category.name
        with transaction.atomic():
            if target is not None:
                products.update(category=target)
                # Sous-catégories rattachées à la cible si c'est une catégorie principale, sinon elles deviennent principales
                children.update(parent=target if target.parent_id is None else None)
            category.delete()
        extra = f' {moved} produit(s) déplacé(s) vers « {target.name} ».' if moved else ''
        messages.success(request, f'Catégorie « {name} » supprimée.{extra}')
        return redirect('dashboard:categories')
    return render(request, 'dashboard/admin/category_delete.html', {
        'category': category, 'n_products': products.count(), 'children': children, 'targets': targets})


# ───────────────────────── Versements ─────────────────────────

@admin_required
def payouts(request):
    from accounting.models import PayoutRequest
    qs = PayoutRequest.objects.select_related('user').order_by('-created_at')
    counts = dict(PayoutRequest.objects.values_list('status').annotate(n=Count('id')))
    status = request.GET.get('status', '')
    if status in dict(PayoutRequest.STATUS_CHOICES):
        qs = qs.filter(status=status)
    else:
        status = ''
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        qs = qs.filter(Q(user__username__icontains=q) | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q)
                       | Q(user__stores__name__icontains=q)).distinct()
    page = _page(request, qs, 20)
    for p in page:
        p.store = p.user.stores.first()
    todo = PayoutRequest.objects.filter(status__in=('pending', 'processing'))
    month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return render(request, 'dashboard/admin/payouts.html', {
        'page': page, 'status': status, 'q': q,
        'tabs': [('', 'Toutes', sum(counts.values()))] + [(c, l, counts.get(c, 0)) for c, l in PayoutRequest.STATUS_CHOICES],
        'todo_amount': _money(todo, 'amount'), 'todo_count': todo.count(),
        'paid_month': _money(PayoutRequest.objects.filter(status='completed', processed_at__gte=month), 'amount'),
    })


@admin_required
def payout_action(request, pk):
    """Traiter une demande : en cours, versée, ou refusée (le montant revient au solde du vendeur). Une seule fois."""
    from django.db import transaction
    from accounting.models import PayoutRequest, SellerWallet, Transaction
    from messaging.utils import notify
    if request.method != 'POST':
        return redirect('dashboard:payouts')
    action, note = request.POST.get('action'), (request.POST.get('notes') or '').strip()[:500]
    with transaction.atomic():
        p = get_object_or_404(PayoutRequest.objects.select_for_update(), pk=pk)
        if p.status not in ('pending', 'processing'):
            messages.error(request, 'Cette demande a déjà été traitée.')
            return _back(request, reverse('dashboard:payouts'))
        amount = f'{p.amount:,.0f} F'.replace(',', ' ')
        wallet, _ = SellerWallet.objects.select_for_update().get_or_create(user=p.user)
        if action == 'processing':
            p.status = 'processing'
            p.save(update_fields=['status'])
            msg = f'Demande de {amount} marquée « en cours ».'
        elif action == 'approve':
            p.status, p.processed_at = 'completed', timezone.now()
            if note:
                p.admin_notes = note
            p.save()
            wallet.pending_balance = max(0, wallet.pending_balance - p.amount)
            wallet.total_withdrawn += p.amount
            wallet.save()
            Transaction.objects.create(user=p.user, type='payout', amount=p.amount, net_amount=p.amount,
                                       status='completed', reference=f'Versement #{p.pk}')
            notify(p.user, 'payment', 'Versement effectué',
                   f'Votre versement de {amount} a été envoyé ({p.get_payment_method_display()}).' + (f' {note}' if note else ''),
                   url='/comptabilite/portefeuille/', send_email=True)
            msg = f'Versement de {amount} marqué comme payé.'
        elif action == 'reject':
            p.status, p.processed_at, p.admin_notes = 'rejected', timezone.now(), note
            p.save()
            wallet.pending_balance = max(0, wallet.pending_balance - p.amount)
            wallet.balance += p.amount
            wallet.save()
            notify(p.user, 'payment', 'Demande de versement refusée',
                   f'Votre demande de {amount} est refusée ; le montant est revenu sur votre solde.' + (f' Motif : {note}' if note else ''),
                   url='/comptabilite/portefeuille/', send_email=True)
            msg = f'Demande de {amount} refusée : le montant est revenu sur le solde du vendeur.'
        else:
            return redirect('dashboard:payouts')
    messages.success(request, msg)
    return _back(request, reverse('dashboard:payouts'))


# ───────────────────────── Dépenses de la plateforme ─────────────────────────

@admin_required
def expenses(request):
    import datetime as dt
    from accounting.models import Expense
    errors, form = {}, {}
    if request.method == 'POST' and request.POST.get('action') == 'delete':
        get_object_or_404(Expense, pk=request.POST.get('expense')).delete()
        messages.success(request, 'Dépense supprimée.')
        return _back(request, reverse('accounting:expenses'))
    if request.method == 'POST':
        form = {k: (request.POST.get(k) or '').strip() for k in ('category', 'description', 'amount', 'date')}
        if form['category'] not in dict(Expense.CATEGORY_CHOICES):
            errors['category'] = 'Choisissez une catégorie.'
        if not form['description']:
            errors['description'] = 'Décrivez la dépense.'
        amount = form['amount'].replace(' ', '')
        if not amount.isdigit() or not 0 < int(amount) <= 10 ** 12:
            errors['amount'] = 'Montant invalide.'
        day = timezone.localdate()
        if form['date']:
            try:
                day = dt.date.fromisoformat(form['date'])
            except ValueError:
                errors['date'] = 'Date invalide.'
        if not errors:
            Expense.objects.create(category=form['category'], description=form['description'][:300], amount=int(amount),
                                   date=day, created_by=request.user, receipt=request.FILES.get('receipt'))
            messages.success(request, 'Dépense enregistrée.')
            return redirect('accounting:expenses')
    qs = Expense.objects.select_related('created_by').order_by('-date', '-created_at')
    period = request.GET.get('period', 'year')
    today = timezone.localdate()
    starts = {'month': today.replace(day=1), 'quarter': today - timedelta(days=90), 'year': today.replace(month=1, day=1), 'all': None}
    if period not in starts:
        period = 'year'
    if starts[period]:
        qs = qs.filter(date__gte=starts[period])
    cat = request.GET.get('category', '')
    if cat in dict(Expense.CATEGORY_CHOICES):
        qs = qs.filter(category=cat)
    else:
        cat = ''
    labels = dict(Expense.CATEGORY_CHOICES)
    by_cat = [(c, labels.get(c, c), t) for c, t in qs.order_by().values_list('category').annotate(t=Sum('amount')).order_by('-t')]
    total = sum(t for _, _, t in by_cat)
    return render(request, 'dashboard/admin/expenses.html', {
        'page': _page(request, qs, 25), 'by_cat': [(c, l, t, round(t * 100 / total) if total else 0) for c, l, t in by_cat],
        'total': total, 'period': period, 'cat': cat, 'categories': Expense.CATEGORY_CHOICES,
        'periods': [('month', 'Ce mois'), ('quarter', '3 derniers mois'), ('year', 'Cette année'), ('all', 'Tout')],
        'errors': errors, 'form': form, 'today': today.isoformat(),
    }, status=400 if errors else 200)
