from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
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

def order_success(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    return render(request, 'orders/success.html', {'order': order})

def create_rfq(request):
    categories = Category.objects.filter(parent__isnull=True)
    if request.method == 'POST':
        # Vérifier si l'utilisateur est connecté pour soumettre
        if not request.user.is_authenticated:
            messages.error(request, 'Vous devez être connecté pour soumettre une demande de devis.')
            return redirect('accounts:login')

        rfq = RFQ.objects.create(
            buyer=request.user,
            product_name=request.POST.get('product_name'),
            category_id=request.POST.get('category') or None,
            description=request.POST.get('description'),
            quantity=int(request.POST.get('quantity', 1)),
            target_price=request.POST.get('target_price') or None,
            unit=request.POST.get('unit', 'pièce'),
        )
        # Notifier tous les vendeurs de la nouvelle demande
        from messaging.utils import notify
        from accounts.models import User as U
        for seller in U.objects.filter(role='seller', is_active=True):
            notify(
                seller, 'rfq',
                'Nouvelle demande de devis',
                f'{request.user.display_name} recherche : {rfq.product_name} ({rfq.quantity} {rfq.unit}).',
                url=f'/commandes/devis/{rfq.pk}/',
            )
        messages.success(request, 'Votre demande de devis a été envoyée !')
        return redirect('orders:my_rfqs')
    return render(request, 'orders/rfq_form.html', {'categories': categories})

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
    if request.user.is_authenticated and hasattr(request.user, 'store'):
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
    if not hasattr(request.user, 'store'):
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
            url=f'/commandes/mes-devis/',
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
    from messaging.utils import notify
    notify(
        quote.seller, 'rfq',
        'Votre devis a été accepté !',
        f'{quote.rfq.buyer.display_name} a accepté votre offre de {quote.total_price} FCFA pour « {quote.rfq.product_name} ».',
        url=f'/dashboard/devis/',
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
    if not hasattr(request.user, 'store'):
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
