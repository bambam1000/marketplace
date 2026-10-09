from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.db.models import Count, Q
from .models import Order, RFQ, Quote
from catalog.models import Category

@login_required
def order_list(request):
    orders = Order.objects.filter(buyer=request.user)
    status = request.GET.get('status')
    if status: orders = orders.filter(status=status)
    return render(request, 'orders/list.html', {'orders': orders})

@login_required
def order_detail(request, order_number):
    if request.user.role == 'admin' or request.user.is_superuser:
        order = get_object_or_404(Order, order_number=order_number)
    else:
        order = get_object_or_404(Order, order_number=order_number, buyer=request.user)
    return render(request, 'orders/detail.html', {'order': order})

@login_required
def order_success(request, order_number):
    """Confirmation de commande : visible seulement par l'acheteur (ou un administrateur)."""
    if request.user.role == 'admin' or request.user.is_superuser:
        order = get_object_or_404(Order, order_number=order_number)
    else:
        order = get_object_or_404(Order, order_number=order_number, buyer=request.user)
    return render(request, 'orders/success.html', {'order': order})

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
    rfqs = RFQ.objects.filter(status='open').annotate(quote_cnt=Count('quotes')).order_by('-created_at')

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
    quotes = rfq.quotes.all()

    # Vérifier si l'utilisateur a déjà soumis un devis
    user_quote = None
    if request.user.is_authenticated and (request.user.store is not None):
        user_quote = quotes.filter(seller=request.user).first()

    return render(request, 'orders/rfq_detail.html', {
        'rfq': rfq,
        'quotes': quotes,
        'user_quote': user_quote,
    })

# Mes RFQ (pour acheteur)
@login_required
def my_rfqs(request):
    """Dashboard des RFQ de l'acheteur"""
    rfqs = RFQ.objects.filter(buyer=request.user).annotate(quote_cnt=Count('quotes'))

    status = request.GET.get('status')
    if status:
        rfqs = rfqs.filter(status=status)

    return render(request, 'orders/my_rfqs.html', {'rfqs': rfqs})

# Soumettre un devis (vendeur)
@login_required
def submit_quote(request, rfq_id):
    """Soumettre un devis pour une RFQ"""
    rfq = get_object_or_404(RFQ, pk=rfq_id)

    # Vérifier que l'utilisateur a une boutique
    if not (request.user.store is not None):
        messages.error(request, 'Vous devez avoir une boutique pour soumettre un devis.')
        return redirect('orders:rfq_detail', rfq_id=rfq_id)

    # Vérifier que la RFQ est ouverte
    if rfq.status != 'open':
        messages.error(request, 'Cette demande de devis n\'est plus ouverte.')
        return redirect('orders:rfq_detail', rfq_id=rfq_id)

    if request.method == 'POST':
        price_per_unit = int(request.POST.get('price_per_unit'))
        total_price = price_per_unit * rfq.quantity

        # Créer ou mettre à jour le devis
        quote, created = Quote.objects.update_or_create(
            rfq=rfq,
            seller=request.user,
            defaults={
                'store': request.user.store,
                'price_per_unit': price_per_unit,
                'total_price': total_price,
                'delivery_time': request.POST.get('delivery_time'),
                'payment_terms': request.POST.get('payment_terms'),
                'description': request.POST.get('description'),
            }
        )

        # Mettre à jour le statut de la RFQ
        if rfq.status == 'open':
            rfq.status = 'quoted'
            rfq.save()

        # Notifier l'acheteur de l'offre reçue
        from messaging.utils import notify
        notify(
            rfq.buyer, 'rfq',
            f'Offre reçue pour « {rfq.product_name} »',
            f'{request.user.store.name} vous propose {total_price} FCFA ({price_per_unit} FCFA/{rfq.unit}).',
            url='/commandes/rfq/mes-demandes/',
            send_email=True,
        )
        messages.success(request, 'Votre devis a été soumis avec succès !')
        return redirect('orders:rfq_detail', rfq_id=rfq_id)

    return render(request, 'orders/submit_quote.html', {'rfq': rfq})

# Accepter un devis (acheteur)
@login_required
def accept_quote(request, quote_id):
    """Accepter un devis"""
    quote = get_object_or_404(Quote, pk=quote_id)

    # Vérifier que c'est bien l'acheteur
    if quote.rfq.buyer != request.user:
        messages.error(request, 'Vous n\'êtes pas autorisé à accepter ce devis.')
        return redirect('orders:my_rfqs')

    # Accepter le devis
    quote.status = 'accepted'
    quote.save()

    # Mettre à jour la RFQ
    quote.rfq.status = 'accepted'
    quote.rfq.save()

    # Rejeter les autres devis
    Quote.objects.filter(rfq=quote.rfq).exclude(pk=quote_id).update(status='rejected')

    # Notifier le vendeur
    from messaging.utils import notify_store
    notify_store(
        quote.store, 'orders.view', 'rfq',
        'Votre devis a été accepté',
        f'{quote.rfq.buyer.display_name} a accepté votre offre de {quote.total_price} FCFA pour « {quote.rfq.product_name} ».',
        url=f'/dashboard/devis/{quote.rfq_id}/',
        send_email=True,
    )
    messages.success(request, f'Vous avez accepté le devis de {quote.store.name}. Le vendeur va vous contacter.')
    return redirect('orders:my_rfqs')

# Rejeter un devis (acheteur)
@login_required
def reject_quote(request, quote_id):
    """Rejeter un devis"""
    quote = get_object_or_404(Quote, pk=quote_id)

    # Vérifier que c'est bien l'acheteur
    if quote.rfq.buyer != request.user:
        messages.error(request, 'Vous n\'êtes pas autorisé à rejeter ce devis.')
        return redirect('orders:my_rfqs')

    quote.status = 'rejected'
    quote.save()

    messages.success(request, 'Le devis a été rejeté.')
    return redirect('orders:my_rfqs')

# Fermer une RFQ (acheteur)
@login_required
def close_rfq(request, rfq_id):
    """Permet à l'acheteur de fermer sa demande de devis"""
    rfq = get_object_or_404(RFQ, pk=rfq_id)

    if rfq.buyer != request.user:
        messages.error(request, 'Vous ne pouvez fermer que vos propres demandes.')
        return redirect('orders:my_rfqs')

    if rfq.status in ['open', 'quoted']:
        rfq.status = 'closed'
        rfq.save()
        messages.success(request, f'Votre demande "{rfq.product_name}" a été fermée.')
    else:
        messages.error(request, 'Cette demande ne peut plus être fermée.')

    return redirect('orders:my_rfqs')

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
