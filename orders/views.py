from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.db.models import Count, Q
from .models import Order, RFQ, Quote
from catalog.models import Category
from accounts.account import account_nav

@login_required
def order_list(request):
    """Mes commandes : onglets avec compteurs, recherche, pagination."""
    from django.core.paginator import Paginator
    from django.db.models import Prefetch
    from accounts.account import ACTIVE_ORDER, search_q
    from .models import OrderItem
    base = Order.objects.filter(buyer=request.user)
    to_pay_q = Q(status='pending', is_paid=False) & ~Q(payment_method='cash')
    filters = {
        'active': Q(status__in=ACTIVE_ORDER),
        'to_pay': to_pay_q,
        'delivered': Q(status='delivered'),
        'cancelled': Q(status__in=('cancelled', 'refunded')),
    }
    counts = base.aggregate(all=Count('id'), **{k: Count('id', filter=f) for k, f in filters.items()})
    tabs = [('', 'Toutes', counts['all']), ('active', 'En cours', counts['active'])]
    if counts['to_pay']:
        tabs.append(('to_pay', 'À payer', counts['to_pay']))
    tabs += [('delivered', 'Livrées', counts['delivered']), ('cancelled', 'Annulées', counts['cancelled'])]

    status = request.GET.get('status', '')
    orders = base
    if status in filters:
        orders = orders.filter(filters[status])
    elif status in dict(Order.STATUS_CHOICES):   # anciens liens ?status=pending…
        orders = orders.filter(status=status)
    else:
        status = ''
    q = (request.GET.get('q') or '').strip()[:100]
    if q:
        orders = orders.filter(search_q(q)).distinct()
    orders = orders.prefetch_related(Prefetch('items', queryset=OrderItem.objects.select_related('product', 'store')))
    page = Paginator(orders, 10).get_page(request.GET.get('page'))
    for o in page:
        o.item_list = list(o.items.all())
        o.store_names = list(dict.fromkeys(i.store.name for i in o.item_list))
        o.to_pay = o.status == 'pending' and not o.is_paid and o.payment_method != 'cash'
    return render(request, 'orders/list.html', {
        'acc': account_nav(request.user, 'orders'), 'page': page, 'orders': page.object_list,
        'tabs': tabs, 'status': status, 'q': q, 'total': counts['all'],
    })

@login_required
def order_detail(request, order_number):
    if request.user.role == 'admin' or request.user.is_superuser:
        order = get_object_or_404(Order, order_number=order_number)
    else:
        order = get_object_or_404(Order, order_number=order_number, buyer=request.user)
    from collections import OrderedDict
    from catalog.models import Review

    # Étapes et leurs dates (historique, sinon date de la commande pour la première)
    dates = {e.status: e.created_at for e in order.events.all()}
    dates.setdefault('pending', order.created_at)
    codes = [c for c, _, _ in ORDER_STEPS]
    current = codes.index(order.status) if order.status in codes else -1
    steps = [{'code': c, 'label': label, 'icon': icon, 'done': i <= current, 'current': i == current,
              'date': dates.get(c) if i <= current else None} for i, (c, label, icon) in enumerate(ORDER_STEPS)]

    # Articles regroupés par boutique
    groups = OrderedDict()
    for it in order.items.select_related('product', 'store'):
        g = groups.setdefault(it.store_id, {'store': it.store, 'items': [], 'total': 0})
        g['items'].append(it)
        g['total'] += it.subtotal

    is_buyer = order.buyer_id == request.user.id
    delivered = order.status == 'delivered'
    product_ids = [i.product_id for g in groups.values() for i in g['items']]
    return render(request, 'orders/detail.html', {
        'order': order, **_payment_ctx(order),
        'steps': steps, 'stopped': order.status in ('cancelled', 'refunded'),
        'stopped_at': dates.get(order.status) or order.updated_at,
        'cancel_note': next((e.note for e in order.events.all() if e.status == 'cancelled' and e.note), ''),
        'groups': list(groups.values()), 'is_buyer': is_buyer,
        'can_cancel': is_buyer and _can_cancel(order),
        'delivered': delivered,
        'reviewed': set(Review.objects.filter(user=request.user, product_id__in=product_ids)
                        .values_list('product_id', flat=True)) if delivered and is_buyer else set(),
        'discounts': (order.discount_amount or 0) + (order.loyalty_discount or 0),
    })


ORDER_STEPS = [
    ('pending', 'Commande passée', 'fa-receipt'),
    ('confirmed', 'Confirmée', 'fa-circle-check'),
    ('processing', 'En préparation', 'fa-box-open'),
    ('shipped', 'Expédiée', 'fa-truck-fast'),
    ('delivered', 'Livrée', 'fa-house-circle-check'),
]


def _can_cancel(order):
    """Le client peut annuler tant que la boutique n'a pas confirmé et que rien n'est payé."""
    return order.status == 'pending' and not order.is_paid and not order.is_direct_sale


def _buyer_order(request, order_number):
    return get_object_or_404(Order, order_number=order_number, buyer=request.user)


@login_required
def cancel_order(request, order_number):
    """Annulation par le client : stock remis, fidélité et code promo rendus, boutiques prévenues."""
    from django.db import transaction
    order = _buyer_order(request, order_number)
    if request.method != 'POST':
        return redirect('orders:detail', order_number=order_number)
    if not _can_cancel(order):
        messages.error(request, "Cette commande ne peut plus être annulée en ligne : la boutique l'a déjà prise en charge. Contactez-la.")
        return redirect('orders:detail', order_number=order_number)
    reason = (request.POST.get('reason') or '').strip()[:200]
    from .services import apply_cancellation
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if not _can_cancel(order):
            return redirect('orders:detail', order_number=order_number)
        per_store = apply_cancellation(order, request.user, reason)
    from messaging.utils import notify_store
    for st, amount in per_store.items():
        msg = f'{request.user.display_name} a annulé sa commande ({int(amount):,} FCFA).'.replace(',', ' ')
        if reason:
            msg += f' Motif : {reason}'
        notify_store(st, 'orders.view', 'order', f'Commande {order.order_number} annulée par le client', msg,
                     url=f'/dashboard/commandes/{order.order_number}/', send_email=True)
    messages.success(request, f'Commande {order.order_number} annulée.')
    return redirect('orders:detail', order_number=order_number)


@login_required
def reorder(request, order_number):
    """Remet dans le panier les produits encore disponibles d'une commande, aux prix actuels."""
    order = _buyer_order(request, order_number)
    if request.method != 'POST':
        return redirect('orders:detail', order_number=order_number)
    from cart.utils import get_cart, save_cart
    cart = get_cart(request)
    added, missing = 0, []
    for it in order.items.select_related('product', 'product__store'):
        p = it.product
        if not p.is_active or p.stock <= 0 or not p.store.is_active:
            missing.append(p.name)
            continue
        key, qty = str(p.pk), min(it.quantity, p.stock)
        if key in cart:
            cart[key]['quantity'] = min(cart[key]['quantity'] + qty, p.stock)
        else:
            cart[key] = {'name': p.name, 'price': int(p.price), 'quantity': qty, 'image': p.image.url if p.image else '',
                         'store': p.store.name, 'store_id': p.store_id, 'color': it.color, 'size': it.size}
        added += 1
    save_cart(request, cart)
    s = 's' if added > 1 else ''
    if added:
        messages.success(request, f'{added} produit{s} ajouté{s} au panier, aux prix actuels.')
    if missing:
        messages.warning(request, 'Plus disponible : ' + ', '.join(missing) + '.')
    if added:
        return redirect('cart:view')
    return redirect('orders:detail', order_number=order_number)


@login_required
def review_item(request, order_number, product_id):
    """Avis sur un produit reçu : commande livrée, un avis par produit."""
    from catalog.models import Review
    order = _buyer_order(request, order_number)
    back = reverse('orders:detail', args=[order_number]) + '#articles'
    if request.method != 'POST' or order.status != 'delivered':
        return redirect(back)
    item = order.items.filter(product_id=product_id).select_related('product').first()
    if not item:
        return redirect(back)
    if Review.objects.filter(user=request.user, product_id=product_id).exists():
        messages.info(request, 'Vous avez déjà donné votre avis sur ce produit.')
        return redirect(back)
    try:
        rating = int(request.POST.get('rating', 0))
    except ValueError:
        rating = 0
    if not 1 <= rating <= 5:
        messages.error(request, 'Choisissez une note de 1 à 5 étoiles.')
        return redirect(back)
    comment = (request.POST.get('comment') or '').strip()[:2000]
    Review.objects.create(product=item.product, user=request.user, name=request.user.display_name,
                          rating=rating, comment=comment)
    messages.success(request, 'Merci ! Votre avis est publié sur la page du produit.')
    return redirect(back)

@login_required
def order_success(request, order_number):
    """Confirmation de commande : visible seulement par l'acheteur (ou un administrateur)."""
    if request.user.role == 'admin' or request.user.is_superuser:
        order = get_object_or_404(Order, order_number=order_number)
    else:
        order = get_object_or_404(Order, order_number=order_number, buyer=request.user)
    return render(request, 'orders/success.html', {'order': order, **_payment_ctx(order)})


def _payment_ctx(order):
    """À qui et comment payer, tant que la commande n'est pas réglée."""
    from billing.payments import METHODS, order_instructions
    method = METHODS.get(order.payment_method)
    show = method and not order.is_paid and order.status not in ('cancelled', 'refunded')
    stores = order.items.values('store').distinct().count()
    return {'pay_method': method, 'pay_info': order_instructions(order) if show else [], 'store_count': stores}

RFQ_UNITS = ['pièce', 'kg', 'tonne', 'sac', 'carton', 'lot', 'palette', 'litre', 'mètre', 'boîte']
RFQ_LIMITS = {'name': (3, 200), 'description': (10, 3000), 'quantity': (1, 10_000_000), 'price': (1, 1_000_000_000)}


def _rfq_categories():
    """Catégories proposées : celles qui ont des produits en ligne (directement ou dans leurs sous-catégories)."""
    from django.db.models import F
    return (Category.objects.filter(parent__isnull=True, is_active=True)
            .annotate(direct=Count('products', filter=Q(products__is_active=True), distinct=True),
                      nested=Count('children__products', filter=Q(children__products__is_active=True), distinct=True))
            .annotate(total=F('direct') + F('nested')).filter(total__gt=0).order_by('order', 'name'))


def _clean_rfq(data):
    """Valide le formulaire. Retourne (valeurs nettoyées, erreurs par champ)."""
    errors, clean = {}, {}
    name = ' '.join(data.get('product_name', '').split())
    lo, hi = RFQ_LIMITS['name']
    if not lo <= len(name) <= hi:
        errors['product_name'] = f'Indiquez le produit recherché ({lo} à {hi} caractères).'
    clean['product_name'] = name

    raw_qty = data.get('quantity', '').strip().replace(' ', '')
    lo, hi = RFQ_LIMITS['quantity']
    if not raw_qty.isdigit() or not lo <= int(raw_qty) <= hi:
        errors['quantity'] = 'Indiquez une quantité entière supérieure à 0.'
    else:
        clean['quantity'] = int(raw_qty)

    unit = data.get('unit', 'pièce')
    clean['unit'] = unit if unit in RFQ_UNITS else 'pièce'

    raw_price = data.get('target_price', '').strip().replace(' ', '')
    clean['target_price'] = None
    if raw_price:
        lo, hi = RFQ_LIMITS['price']
        if not raw_price.isdigit() or not lo <= int(raw_price) <= hi:
            errors['target_price'] = 'Le prix cible doit être un montant positif en FCFA (ou laissez vide).'
        else:
            clean['target_price'] = int(raw_price)

    clean['category'] = None
    raw_cat = data.get('category', '')
    if raw_cat:
        cat = Category.objects.filter(pk=raw_cat, is_active=True).first() if raw_cat.isdigit() else None
        if cat is None:
            errors['category'] = 'Catégorie inconnue.'
        clean['category'] = cat

    description = data.get('description', '').strip()
    lo, hi = RFQ_LIMITS['description']
    if not lo <= len(description) <= hi:
        errors['description'] = f'Décrivez votre besoin ({lo} à {hi} caractères) : matériaux, dimensions, délai, lieu de livraison…'
    clean['description'] = description
    return clean, errors


def _notify_rfq(rfq):
    """Prévient les boutiques concernées (celles qui vendent dans la catégorie, sinon toutes) et leurs employés."""
    from messaging.utils import notify_store
    from store.models import Store
    stores = Store.objects.filter(is_active=True, owner__is_active=True).exclude(owner=rfq.buyer)
    if rfq.category_id:
        in_category = stores.filter(Q(products__category=rfq.category) | Q(products__category__parent=rfq.category),
                                    products__is_active=True).distinct()
        if in_category.exists():
            stores = in_category
    title = 'Nouvelle demande de devis'
    text = f'{rfq.buyer.display_name} recherche : {rfq.product_name} ({rfq.quantity} {rfq.unit}).'
    if rfq.target_price:
        text += f' Prix cible : {int(rfq.target_price):,} FCFA / {rfq.unit}.'.replace(',', ' ')
    sent = 0
    for store in stores.select_related('owner'):
        sent += len(notify_store(store, 'orders.view', 'rfq', title, text, url=f'/dashboard/devis/{rfq.pk}/'))
    return sent


def create_rfq(request):
    """Demande de devis : le formulaire est visible par tous ; il faut être connecté pour l'envoyer."""
    from datetime import timedelta
    from django.utils import timezone
    values = {'product_name': request.GET.get('produit', '')[:200], 'quantity': request.GET.get('quantite', '')[:12],
              'unit': 'pièce', 'target_price': '', 'category': request.GET.get('categorie', ''), 'description': ''}
    errors = {}
    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.info(request, 'Connectez-vous pour envoyer votre demande de devis.')
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        values = {k: request.POST.get(k, '') for k in values}
        clean, errors = _clean_rfq(request.POST)
        if not errors:
            recent = RFQ.objects.filter(buyer=request.user, product_name__iexact=clean['product_name'],
                                        quantity=clean['quantity'], created_at__gte=timezone.now() - timedelta(minutes=2)).first()
            if recent:
                messages.info(request, 'Cette demande a déjà été envoyée.')
                return redirect('orders:my_rfqs')
            rfq = RFQ.objects.create(buyer=request.user, **clean)
            sent = _notify_rfq(rfq)
            messages.success(request, 'Votre demande de devis a été envoyée'
                             + (f' à {sent} vendeur{"s" if sent > 1 else ""} concerné{"s" if sent > 1 else ""}.' if sent else '.'))
            return redirect('orders:my_rfqs')
        messages.error(request, 'Vérifiez les champs signalés.')
    return render(request, 'orders/rfq_form.html', {
        'categories': _rfq_categories(), 'units': RFQ_UNITS, 'values': values, 'errors': errors,
        'limits': RFQ_LIMITS,
        'my_count': RFQ.objects.filter(buyer=request.user).count() if request.user.is_authenticated else 0,
    })

# Liste des RFQ pour les vendeurs
def rfq_list(request):
    """Liste des RFQ ouvertes pour les vendeurs"""
    from .rfq_services import ACCEPTING
    rfqs = RFQ.objects.filter(status__in=ACCEPTING).annotate(quote_cnt=Count('quotes')).order_by('-created_at')

    # Filtres
    category = request.GET.get('category')
    if category:
        rfqs = rfqs.filter(category_id=category)

    search = request.GET.get('q')
    if search:
        rfqs = rfqs.filter(Q(product_name__icontains=search) | Q(description__icontains=search))

    categories = Category.objects.filter(parent__isnull=True)

    return render(request, 'orders/rfq_list.html', {
        'rfqs': rfqs,
        'categories': categories,
    })

# Détail d'une RFQ
@login_required
def rfq_detail(request, rfq_id):
    """Détail d'une RFQ avec les devis proposés"""
    rfq = get_object_or_404(RFQ, pk=rfq_id)
    quotes = rfq.quotes.select_related('store').order_by('price_per_unit', 'created_at')

    # Vérifier si l'utilisateur a déjà soumis un devis
    user_quote = None
    if request.user.is_authenticated and (request.user.store is not None):
        user_quote = quotes.filter(seller=request.user).first()

    return render(request, 'orders/rfq_detail.html', {
        'rfq': rfq,
        'quotes': quotes,
        'user_quote': user_quote,
        'accepting': rfq.status in ('open', 'quoted'),
        'best_price': min((q.price_per_unit for q in quotes if q.status != 'rejected'), default=None),
    })

# Mes RFQ (pour acheteur)
@login_required
def my_rfqs(request):
    """Demandes de devis de l'acheteur, avec leurs offres."""
    from django.db.models import Min, Prefetch
    base = RFQ.objects.filter(buyer=request.user)
    counts = dict(base.values_list('status').annotate(n=Count('id')))
    status = request.GET.get('status', '')
    if status not in dict(RFQ.STATUS_CHOICES):
        status = ''
    rfqs = (base.filter(status=status) if status else base).select_related('category').annotate(
        quote_cnt=Count('quotes', distinct=True),
        pending_cnt=Count('quotes', filter=Q(quotes__status='pending'), distinct=True),
        best_price=Min('quotes__price_per_unit', filter=~Q(quotes__status='rejected')),
    ).prefetch_related(Prefetch('quotes', queryset=Quote.objects.filter(status='accepted').select_related('store', 'seller'),
                                to_attr='accepted_quotes'))
    tabs = [('', 'Toutes', sum(counts.values()))] + [(code, label, counts.get(code, 0)) for code, label in RFQ.STATUS_CHOICES]
    return render(request, 'orders/my_rfqs.html', {
        'rfqs': rfqs, 'tabs': tabs, 'status': status,
        'to_review': base.filter(quotes__status='pending', status__in=('open', 'quoted')).distinct().count(),
        'acc': account_nav(request.user, 'rfqs'),
    })

# Soumettre un devis (vendeur)
@login_required
def submit_quote(request, rfq_id):
    """Soumettre ou modifier une offre pour une demande de devis."""
    from .rfq_services import QuoteError, submit_quote as save_quote
    rfq = get_object_or_404(RFQ, pk=rfq_id)
    if request.method == 'POST':
        try:
            _, created = save_quote(rfq, request.user, request.POST)
        except QuoteError as exc:
            messages.error(request, str(exc))
            return redirect('orders:submit_quote', rfq_id=rfq_id) if rfq.status in ('open', 'quoted') else redirect('orders:rfq_detail', rfq_id=rfq_id)
        messages.success(request, 'Votre offre a été envoyée à l\'acheteur.' if created else 'Votre offre a été mise à jour.')
        return redirect('orders:rfq_detail', rfq_id=rfq_id)
    if request.user.store is None:
        messages.error(request, 'Vous devez avoir une boutique pour proposer une offre.')
        return redirect('orders:rfq_detail', rfq_id=rfq_id)
    if rfq.status not in ('open', 'quoted'):
        messages.error(request, "Cette demande n'accepte plus d'offres.")
        return redirect('orders:rfq_detail', rfq_id=rfq_id)
    return render(request, 'orders/submit_quote.html', {'rfq': rfq})

# Accepter / refuser une offre, fermer une demande (acheteur) — en POST uniquement
def _rfq_action(request, action, obj, success):
    from .rfq_services import QuoteError
    if request.method != 'POST':
        return redirect('orders:my_rfqs')
    try:
        action(obj, request.user)
        messages.success(request, success)
    except QuoteError as exc:
        messages.error(request, str(exc))
    rfq_id = obj.rfq_id if isinstance(obj, Quote) else obj.pk
    nxt = request.POST.get('next', '')
    return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else reverse('orders:rfq_detail', args=[rfq_id]))


@login_required
def accept_quote(request, quote_id):
    from .rfq_services import accept_quote as do_accept
    quote = get_object_or_404(Quote.objects.select_related('rfq', 'store'), pk=quote_id)
    return _rfq_action(request, do_accept, quote,
                       f'Vous avez accepté l\'offre de {quote.store.name}. La boutique est prévenue et va vous contacter.')


@login_required
def reject_quote(request, quote_id):
    from .rfq_services import reject_quote as do_reject
    quote = get_object_or_404(Quote.objects.select_related('rfq', 'store'), pk=quote_id)
    return _rfq_action(request, do_reject, quote, 'Offre refusée.')


@login_required
def close_rfq(request, rfq_id):
    from .rfq_services import close_rfq as do_close
    rfq = get_object_or_404(RFQ, pk=rfq_id)
    return _rfq_action(request, do_close, rfq, f'Votre demande « {rfq.product_name} » est fermée.')

# Mes devis (pour vendeur)
@login_required
def my_quotes(request):
    """Dashboard des devis soumis par le vendeur"""
    if not (request.user.store is not None):
        messages.error(request, 'Vous devez avoir une boutique.')
        return redirect('dashboard:index')

    all_quotes = Quote.objects.filter(seller=request.user).select_related('rfq', 'store')

    # Statistiques
    total_quotes = all_quotes.count()
    pending_quotes = all_quotes.filter(status='pending').count()
    accepted_quotes = all_quotes.filter(status='accepted').count()
    rejected_quotes = all_quotes.filter(status='rejected').count()

    # Filtrer par statut si demandé
    quotes = all_quotes
    status = request.GET.get('status')
    if status:
        quotes = quotes.filter(status=status)

    return render(request, 'orders/my_quotes.html', {
        'quotes': quotes,
        'total_quotes': total_quotes,
        'pending_quotes': pending_quotes,
        'accepted_quotes': accepted_quotes,
        'rejected_quotes': rejected_quotes,
    })
