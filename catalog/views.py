from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Avg, Count, Min, Max
from .models import Category, Product, Review, Wishlist, FlashDeal, HeroBanner, ContactMessage
from store.models import Store

def home(request):
    from django.utils import timezone
    from marketing.models import Campaign
    categories = Category.objects.filter(parent__isnull=True, is_active=True)[:10]
    banners = HeroBanner.objects.filter(is_active=True)
    flash_products = Product.objects.filter(is_flash_deal=True, is_active=True)[:6]
    featured = Product.objects.filter(is_featured=True, is_active=True)[:12]
    new_products = Product.objects.filter(is_active=True).order_by('-created_at')[:12]
    top_stores = Store.objects.filter(is_active=True)[:10]
    # Campagnes actives affichées sur la vitrine
    now = timezone.now()
    active_campaigns = Campaign.objects.filter(
        status='active', start_date__lte=now, end_date__gte=now
    ).select_related('store', 'promo_code')[:4]
    for camp in active_campaigns:
        Campaign.objects.filter(pk=camp.pk).update(views_count=camp.views_count + 1)
    from . import recommend
    prof = recommend.profile(request)
    return render(request, 'catalog/home.html', {
        'for_you': recommend.for_you(request, limit=12) if not prof.empty else [],
        'recently_viewed': recommend.recently_viewed(request, limit=6),
        'popular_products': recommend.popular(12) if prof.empty else [],
        'categories': categories,
        'banners': banners,
        'flash_products': flash_products,
        'featured_products': featured,
        'new_products': new_products,
        'top_stores': top_stores,
        'active_campaigns': active_campaigns,
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
        _record_search(request, q, products)
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
    from . import recommend
    recommend.record(request, 'category', category=category)
    return render(request, 'catalog/category.html', {
        'category': category,
        'subcategories': subcategories,
        'products': products,
    })

def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    product.views_count += 1
    product.save(update_fields=['views_count'])
    from . import recommend
    recommend.record(request, 'view', product=product)
    related = recommend.similar(product, limit=6)
    together = recommend.bought_together([product.pk], limit=6)
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
        'bought_together': together,
        'store_products': store_products,
        'reviews': reviews,
        'rating_dist': rating_dist,
        'in_wishlist': in_wishlist,
    })

def _record_search(request, q, products):
    """Note la recherche avec la catégorie la plus représentée dans les résultats."""
    from collections import Counter
    from . import recommend
    cats = Counter(p.category_id for p in list(products[:20]))
    top = cats.most_common(1)
    cat = Category.objects.filter(pk=top[0][0]).first() if top else None
    recommend.record(request, 'search', category=cat, query=q)


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

    if q:
        _record_search(request, q, products)
    suggestions = []
    if q and not products:
        from . import recommend
        suggestions = recommend.for_you(request, limit=8)
    context = {
        'suggestions': suggestions,
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

WISHLIST_SORTS = {
    'recent': ('Ajoutés récemment', ['-created_at']),
    'prix': ('Prix croissant', ['product__price']),
    '-prix': ('Prix décroissant', ['-product__price']),
    'nom': ('Nom', ['product__name']),
}
WISHLIST_FILTERS = [('tous', 'Tous'), ('dispo', 'Disponibles'), ('baisse', 'Prix en baisse'), ('promo', 'En promotion')]


def _safe_next(request, default):
    """Adresse de retour : seulement une page de ce site (jamais un site externe)."""
    from django.utils.http import url_has_allowed_host_and_scheme
    for candidate in (request.POST.get('next'), request.GET.get('next'), request.META.get('HTTP_REFERER')):
        if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()},
                                                         require_https=request.is_secure()):
            return candidate
    return default


@login_required
def wishlist_view(request):
    from django.urls import reverse
    sort = request.GET.get('tri', 'recent')
    if sort not in WISHLIST_SORTS:
        sort = 'recent'
    current = request.GET.get('filtre', 'tous')
    items = list(Wishlist.objects.filter(user=request.user)
                 .select_related('product', 'product__store', 'product__category')
                 .order_by(*WISHLIST_SORTS[sort][1]))
    for w in items:
        p = w.product
        w.available = p.is_active and p.stock > 0
        w.archived = not p.is_active
    groups = {
        'tous': items,
        'dispo': [w for w in items if w.available],
        'baisse': [w for w in items if w.price_drop and not w.archived],
        'promo': [w for w in items if w.product.discount_percent and not w.archived],
    }
    if current not in groups:
        current = 'tous'
    suggestions = []
    if not items:
        suggestions = (Product.objects.filter(is_active=True, stock__gt=0).select_related('store')
                       .order_by('-orders_count')[:8])
    return render(request, 'catalog/wishlist.html', {
        'items': groups[current], 'all_count': len(items), 'current': current, 'sort': sort,
        'sorts': [(k, v[0]) for k, v in WISHLIST_SORTS.items()],
        'filters': [(k, label, len(groups[k])) for k, label in WISHLIST_FILTERS],
        'available_count': len(groups['dispo']),
        'total_value': sum(int(w.product.price) for w in groups['dispo']),
        'saved': sum(w.price_drop for w in groups['baisse']),
        'suggestions': suggestions, 'here': reverse('catalog:wishlist'),
    })


@login_required
def toggle_wishlist(request, product_id):
    """Ajoute le produit aux favoris, ou l'en retire s'il y est déjà. Répond en JSON aux appels AJAX."""
    product = get_object_or_404(Product, pk=product_id)
    wish = Wishlist.objects.filter(user=request.user, product=product).first()
    if wish:
        wish.delete()
        added, text = False, f'« {product.name} » retiré de vos favoris.'
    else:
        if not product.is_active:
            messages.error(request, "Ce produit n'est plus disponible.")
            return redirect(_safe_next(request, 'catalog:wishlist'))
        Wishlist.objects.create(user=request.user, product=product)
        from . import recommend
        recommend.record(request, 'wishlist', product=product)
        added, text = True, f'« {product.name} » ajouté à vos favoris.'
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'added': added, 'count': Wishlist.objects.filter(user=request.user).count(), 'message': text})
    (messages.success if added else messages.info)(request, text)
    return redirect(_safe_next(request, 'home'))


@login_required
def wishlist_add_all_to_cart(request):
    """Met dans le panier tous les favoris disponibles (quantité minimale de commande respectée)."""
    if request.method != 'POST':
        return redirect('catalog:wishlist')
    from cart.views import get_cart, save_cart
    cart = get_cart(request)
    added = 0
    for w in Wishlist.objects.filter(user=request.user).select_related('product', 'product__store'):
        p = w.product
        if not p.is_active or p.stock <= 0 or str(p.pk) in cart:
            continue
        cart[str(p.pk)] = {'name': p.name, 'price': int(p.price), 'quantity': max(1, p.min_order),
                           'image': p.image.url if p.image else '', 'store': p.store.name, 'store_id': p.store.id,
                           'color': '', 'size': ''}
        added += 1
    save_cart(request, cart)
    if added:
        s = 's' if added > 1 else ''
        messages.success(request, f'{added} produit{s} ajouté{s} au panier.')
    else:
        messages.info(request, 'Tous vos favoris disponibles sont déjà dans le panier.')
    return redirect('cart:view')

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
    # Chiffres réels de la plateforme (pas de chiffres « vitrine »)
    products = Product.objects.filter(is_active=True, store__is_active=True)
    stats = {
        'stores': Store.objects.filter(is_active=True, products__is_active=True).distinct().count(),
        'products': products.count(),
        'categories': Category.objects.filter(is_active=True, products__in=products).distinct().count(),
    }
    return render(request, 'catalog/about.html', {'stats': stats})

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
    """Questions fréquentes. Les réponses décrivent ce que fait réellement la plateforme."""
    from django.urls import reverse
    from cart.views import FREE_SHIPPING_FROM, SHIPPING_FEE
    money = lambda n: f'{n:,} F'.replace(',', ' ')  # noqa: E731
    link = lambda name, label, *args: f'<a href="{reverse(name, args=args)}">{label}</a>'  # noqa: E731
    from billing.payments import METHODS
    payments = ', '.join(m['label'] for m in METHODS.values())
    sections = [
        ('commander', 'fa-bag-shopping', 'Commander', [
            ('Comment passer une commande ?',
             f"Ajoutez les produits au panier, puis validez-le : indiquez votre adresse de livraison et votre moyen de paiement. "
             f"Il faut un compte pour commander ; l'{link('accounts:register', 'inscription')} est gratuite."),
            ('Comment suivre ma commande ?',
             f"Dans {link('orders:list', 'Mes commandes')}, chaque commande indique son étape : en attente, confirmée, en traitement, "
             f"expédiée puis livrée. Vous êtes prévenu à chaque changement, sur le site et par e-mail."),
            ('Puis-je commander chez plusieurs boutiques en même temps ?',
             "Oui. Votre panier peut contenir des produits de plusieurs boutiques ; chacune prépare et expédie sa partie de la commande."),
            ('Comment modifier ou annuler une commande ?',
             f"Écrivez à la boutique depuis la page du produit ou votre {link('messaging:inbox', 'messagerie')} le plus tôt possible : "
             f"tant que la commande n'est pas expédiée, elle peut l'ajuster ou l'annuler."),
        ]),
        ('livraison', 'fa-truck-fast', 'Livraison', [
            ('Combien coûte la livraison ?',
             f"La livraison coûte {money(SHIPPING_FEE)} par commande, et elle est offerte dès {money(FREE_SHIPPING_FROM)} d'achats. "
             f"Le montant exact est affiché dans votre panier avant de valider."),
            ('Quels sont les délais de livraison ?',
             "Ils dépendent de la boutique et de votre adresse. La boutique vous confirme le délai lorsqu'elle accepte votre commande ; "
             "n'hésitez pas à le lui demander avant d'acheter via la messagerie."),
            ("Que faire si ma commande n'arrive pas ?",
             f"Contactez d'abord la boutique depuis votre {link('messaging:inbox', 'messagerie')}. Sans réponse de sa part, "
             f"{link('contact', 'écrivez-nous')} en indiquant votre numéro de commande."),
        ]),
        ('paiement', 'fa-wallet', 'Paiement', [
            ('Quels moyens de paiement sont acceptés ?',
             f"Chaque boutique choisit ceux qu'elle accepte parmi : {payments}. Au moment de commander, vous ne voyez que "
             f"les moyens acceptés par toutes les boutiques de votre panier."),
            ('Quand mon paiement est-il pris en compte ?',
             "La boutique confirme la réception de votre paiement ; votre commande passe alors à l'étape suivante. "
             "Avec le paiement à la livraison, vous réglez au moment de recevoir votre colis."),
            ('Comment utiliser un code promo ?',
             "Saisissez-le dans votre panier avant de valider la commande. La réduction s'applique aux produits de la boutique "
             "qui a créé le code."),
            ('Comment fonctionne la fidélité ?',
             "Certaines boutiques récompensent leurs clients réguliers : selon vos achats chez elles, vous atteignez un niveau "
             "(Argent, Or, Platine) qui donne droit à une remise appliquée automatiquement dans votre panier. "
             "Elle ne se cumule pas avec un code promo."),
        ]),
        ('retours', 'fa-rotate-left', 'Retours et problèmes', [
            ('Mon produit ne correspond pas : que faire ?',
             f"Contactez la boutique depuis votre {link('messaging:inbox', 'messagerie')}, avec des photos si possible. Chaque boutique "
             f"fixe ses conditions d'échange et de remboursement ; demandez-les avant d'acheter si c'est important pour vous."),
            ("La boutique ne me répond pas",
             f"{link('contact', 'Écrivez-nous')} en précisant votre numéro de commande et l'échange avec la boutique : nous vous aidons à trouver une solution avec elle."),
        ]),
        ('devis', 'fa-file-signature', 'Demandes de devis', [
            ("À quoi sert une demande de devis ?",
             f"Pour un achat en quantité, {link('orders:create_rfq', 'décrivez votre besoin')} : les boutiques concernées vous "
             f"envoient leurs offres avec prix, délai et conditions de paiement."),
            ('Comment choisir une offre ?',
             f"Dans {link('orders:my_rfqs', 'Mes demandes de devis')}, comparez les offres (la moins chère est signalée), contactez les "
             f"boutiques si besoin, puis acceptez celle qui vous convient : les autres sont automatiquement refusées."),
            ('Puis-je arrêter de recevoir des offres ?',
             "Oui, fermez la demande depuis « Mes demandes de devis » : elle ne reçoit plus d'offres."),
        ]),
        ('compte', 'fa-user-shield', 'Compte et sécurité', [
            ("J'ai oublié mon mot de passe",
             f"Utilisez « {link('accounts:password_reset', 'Mot de passe oublié')} » sur la page de connexion : vous recevez un lien "
             f"par e-mail pour en choisir un nouveau."),
            ('Comment gérer mes notifications ?',
             f"Dans vos {link('messaging:notifications', 'notifications')}, le panneau « Préférences » permet de choisir ce que vous "
             f"recevez sur le site, par e-mail et sur WhatsApp."),
            ("Puis-je installer Comptoir sur mon téléphone ?",
             "Oui. Sur Android, touchez « Installer » quand le site vous le propose ; sur iPhone, utilisez Partager puis "
             "« Sur l'écran d'accueil »."),
            ('Que faites-vous de mes données ?',
             f"Elles servent à traiter vos commandes et vos échanges avec les boutiques. Tout est expliqué dans notre "
             f"{link('accounts:privacy', 'politique de confidentialité')}."),
        ]),
        ('vendre', 'fa-store', 'Vendre sur Comptoir', [
            ('Comment devenir vendeur ?',
             f"Créez un compte vendeur puis choisissez une formule d'abonnement adaptée à votre activité. Tout est détaillé sur la "
             f"page {link('seller_landing', 'Vendre sur Comptoir')}."),
            ('Quelle commission prend Comptoir ?',
             "Le taux dépend de votre formule et ne s'applique qu'aux ventes passées sur le site ; les ventes en caisse et les "
             "ventes directes que vous enregistrez n'en ont pas."),
            ('Où trouver plus de réponses pour les vendeurs ?',
             f"Les questions des vendeurs (paiements, caisse, employés, WhatsApp…) sont sur la "
             f"{link('seller_landing', 'page vendeurs')}."),
        ]),
    ]
    from django.utils.html import strip_tags
    from django.utils.safestring import mark_safe
    sections = [{'id': sid, 'icon': icon, 'title': title,
                 'items': [{'q': q, 'a': mark_safe(a), 'plain': strip_tags(a), 'id': f'{sid}-{i + 1}'} for i, (q, a) in enumerate(items)]}
                for sid, icon, title, items in sections]
    return render(request, 'catalog/faq.html', {'sections': sections, 'count': sum(len(s['items']) for s in sections)})


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
            'icon': 'fa-tag'
        })

    for store in stores:
        suggestions.append({
            'type': 'store',
            'id': store['id'],
            'name': store['name'],
            'url': f"/vendeur/{store['slug']}/",
            'city': store['city'],
            'icon': 'fa-store'
        })

    return JsonResponse({'suggestions': suggestions})


def seller_landing(request):
    """Landing page de présentation pour les vendeurs"""
    from billing.models import SubscriptionPlan
    plans = SubscriptionPlan.objects.filter(is_active=True)
    return render(request, 'catalog/seller_landing.html', {'plans': plans})
