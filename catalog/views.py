from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Avg, Count, Min, Max
from .models import Category, Product, Review, Wishlist, FlashDeal, HeroBanner, ContactMessage
from store.models import Store

def home(request):
    categories = Category.objects.filter(parent__isnull=True, is_active=True)[:10]
    banners = HeroBanner.objects.filter(is_active=True)
    flash_products = Product.objects.filter(is_flash_deal=True, is_active=True)[:6]
    featured = Product.objects.filter(is_featured=True, is_active=True)[:12]
    new_products = Product.objects.filter(is_active=True).order_by('-created_at')[:12]
    top_stores = Store.objects.filter(is_active=True)[:10]
    return render(request, 'catalog/home.html', {
        'categories': categories,
        'banners': banners,
        'flash_products': flash_products,
        'featured_products': featured,
        'new_products': new_products,
        'top_stores': top_stores,
    })

def product_list(request):
    products = Product.objects.filter(is_active=True).select_related('store', 'category')
    categories = Category.objects.filter(parent__isnull=True, is_active=True)
    
    cat_slug = request.GET.get('category')
    q = request.GET.get('q', '')
    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    rating = request.GET.get('rating')
    sort = request.GET.get('sort', '-created_at')
    verified = request.GET.get('verified')
    
    if cat_slug:
        cat = Category.objects.filter(slug=cat_slug).first()
        if cat:
            if cat.is_parent:
                child_ids = cat.children.values_list('id', flat=True)
                products = products.filter(Q(category=cat) | Q(category__in=child_ids))
            else:
                products = products.filter(category=cat)
    if q:
        products = products.filter(Q(name__icontains=q) | Q(description__icontains=q))
    if min_price:
        products = products.filter(price__gte=min_price)
    if max_price:
        products = products.filter(price__lte=max_price)
    if verified:
        products = products.filter(store__is_verified=True)
    
    sort_map = {
        'price': 'price', '-price': '-price', 'name': 'name',
        '-created_at': '-created_at', 'popular': '-orders_count', 'rating': '-views_count'
    }
    products = products.order_by(sort_map.get(sort, '-created_at'))
    
    return render(request, 'catalog/product_list.html', {
        'products': products[:60],
        'categories': categories,
        'current_category': cat_slug,
        'search_query': q,
        'sort': sort,
        'total_count': products.count(),
    })

def category_view(request, slug):
    category = get_object_or_404(Category, slug=slug)
    if category.is_parent:
        child_ids = category.children.values_list('id', flat=True)
        products = Product.objects.filter(
            Q(category=category) | Q(category__in=child_ids), is_active=True
        )
    else:
        products = Product.objects.filter(category=category, is_active=True)
    subcategories = category.children.filter(is_active=True) if category.is_parent else []
    return render(request, 'catalog/category.html', {
        'category': category,
        'subcategories': subcategories,
        'products': products,
    })

def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    product.views_count += 1
    product.save(update_fields=['views_count'])
    related = Product.objects.filter(category=product.category, is_active=True).exclude(pk=product.pk)[:6]
    store_products = product.store.products.filter(is_active=True).exclude(pk=product.pk)[:6]
    reviews = product.reviews.all()[:20]
    all_reviews = product.reviews.all()
    rating_dist = {}
    for i in range(1, 6):
        rating_dist[i] = all_reviews.filter(rating=i).count()
    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()
    return render(request, 'catalog/product_detail.html', {
        'product': product,
        'related_products': related,
        'store_products': store_products,
        'reviews': reviews,
        'rating_dist': rating_dist,
        'in_wishlist': in_wishlist,
    })

def search_view(request):
    q = request.GET.get('q', '')
    search_type = request.GET.get('type', 'all')  # all, products, stores

    products = []
    stores = []

    if q:
        # Recherche de produits
        if search_type in ['all', 'products']:
            products = Product.objects.filter(
                Q(name__icontains=q) |
                Q(description__icontains=q) |
                Q(store__name__icontains=q),
                is_active=True
            ).select_related('store', 'category')[:40]

        # Recherche de boutiques
        if search_type in ['all', 'stores']:
            stores = Store.objects.filter(
                Q(name__icontains=q) |
                Q(description__icontains=q) |
                Q(city__icontains=q),
                is_active=True
            )[:20]

    context = {
        'products': products,
        'stores': stores,
        'query': q,
        'search_type': search_type,
        'total_products': len(products),
        'total_stores': len(stores),
    }

    return render(request, 'catalog/search.html', context)

def flash_deals(request):
    products = Product.objects.filter(is_flash_deal=True, is_active=True)
    return render(request, 'catalog/flash_deals.html', {'products': products})

def promotions(request):
    products = Product.objects.filter(is_active=True, old_price__isnull=False).exclude(old_price=0)
    return render(request, 'catalog/promotions.html', {'products': products})

@login_required
def wishlist_view(request):
    items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__store')
    return render(request, 'catalog/wishlist.html', {'items': items})

@login_required
def toggle_wishlist(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    wish, created = Wishlist.objects.get_or_create(user=request.user, product=product)
    if not created:
        wish.delete()
        messages.info(request, 'Retiré de la wishlist.')
    else:
        messages.success(request, 'Ajouté à la wishlist !')
    return redirect(request.META.get('HTTP_REFERER', 'home'))

def add_review(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    if request.method == 'POST':
        Review.objects.create(
            product=product,
            user=request.user if request.user.is_authenticated else None,
            name=request.POST.get('name', 'Anonyme'),
            rating=int(request.POST.get('rating', 5)),
            comment=request.POST.get('comment', ''),
        )
        messages.success(request, 'Merci pour votre avis !')
    return redirect('catalog:product_detail', slug=product.slug)

def about(request):
    return render(request, 'catalog/about.html')

def contact(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()

        if name and email and subject and message:
            ContactMessage.objects.create(
                name=name,
                email=email,
                phone=phone,
                subject=subject,
                message=message
            )
            messages.success(request, '✅ Message envoyé avec succès ! Nous vous répondrons sous 24h.')
        else:
            messages.error(request, '❌ Veuillez remplir tous les champs obligatoires.')

        return redirect('contact')
    return render(request, 'catalog/contact.html')

def faq(request):
    faq_data = {
        '📦 Commandes & Livraison': [
            ('Comment passer une commande ?', 'Parcourez les produits, ajoutez au panier, puis finalisez avec vos infos de livraison et mode de paiement.'),
            ('Quels sont les délais de livraison ?', 'Douala: 24h. Yaoundé: 24-48h. Autres villes: 48-72h. Zones rurales: 3-5 jours.'),
            ('Combien coûte la livraison ?', 'Gratuite dès 50,000 FCFA. Sinon 2,000 F (Douala/Yaoundé) ou 3,500 F (autres villes).'),
        ],
        '💳 Paiement': [
            ('Quels moyens de paiement ?', 'MTN MoMo, Orange Money, carte bancaire (Visa/Mastercard), virement, et cash à la livraison.'),
            ('Le paiement est-il sécurisé ?', 'Oui ! Transactions chiffrées via les passerelles officielles. Trade Assurance sur chaque commande.'),
        ],
        '🏪 Vendeurs': [
            ('Comment devenir vendeur ?', 'Inscrivez-vous en tant que vendeur, c\'est gratuit. Créez votre boutique et ajoutez vos produits.'),
            ('Qu\'est-ce que la Trade Assurance ?', 'C\'est notre garantie acheteur : si le produit n\'est pas conforme, vous êtes remboursé.'),
            ('Comment fonctionne le RFQ ?', 'Soumettez une demande de devis avec vos besoins. Les vendeurs vous envoient leurs meilleures offres.'),
        ],
        '🔄 Retours': [
            ('Politique de retour ?', 'Retour possible sous 7 jours. Produit non utilisé, emballage d\'origine. Contactez-nous par WhatsApp.'),
            ('Délai de remboursement ?', '3-5 jours ouvrés après validation du retour, via le même moyen de paiement.'),
        ],
    }
    return render(request, 'catalog/faq.html', {'faq_data': faq_data})


def search_autocomplete(request):
    """API pour l'autocomplete de la recherche"""
    q = request.GET.get('q', '')

    if len(q) < 2:
        return JsonResponse({'suggestions': []})

    # Rechercher produits et boutiques
    products = Product.objects.filter(
        Q(name__icontains=q),
        is_active=True
    ).values('id', 'name', 'slug', 'price')[:5]

    stores = Store.objects.filter(
        Q(name__icontains=q),
        is_active=True
    ).values('id', 'name', 'slug', 'city')[:5]

    # Formater les résultats
    suggestions = []

    for product in products:
        suggestions.append({
            'type': 'product',
            'id': product['id'],
            'name': product['name'],
            'url': f"/boutique/produit/{product['slug']}/",
            'price': float(product['price']),
            'icon': '🏷️'
        })

    for store in stores:
        suggestions.append({
            'type': 'store',
            'id': store['id'],
            'name': store['name'],
            'url': f"/vendeur/{store['slug']}/",
            'city': store['city'],
            'icon': '🏪'
        })

    return JsonResponse({'suggestions': suggestions})
