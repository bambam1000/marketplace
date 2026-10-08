from django.db.models import Count, F, Q

from catalog.models import Category, Wishlist


def categories_context(request):
    wishlist_count = 0
    if request.user.is_authenticated:
        wishlist_count = Wishlist.objects.filter(user=request.user).count()
    # Menu : seulement les catégories qui ont des produits en ligne (directement ou dans leurs sous-catégories),
    # les mieux fournies d'abord — les catégories vides n'encombrent pas le menu mobile ni la recherche.
    nav_categories = (Category.objects.filter(parent__isnull=True, is_active=True)
                      .annotate(direct=Count('products', filter=Q(products__is_active=True), distinct=True),
                                nested=Count('children__products', filter=Q(children__products__is_active=True), distinct=True))
                      .annotate(total=F('direct') + F('nested'))
                      .filter(total__gt=0)
                      .order_by('order', '-total', 'name')
                      .prefetch_related('children')[:10])
    return {
        'nav_categories': nav_categories,
        'wishlist_count': wishlist_count,
    }
