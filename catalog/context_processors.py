from catalog.models import Category, Wishlist

def categories_context(request):
    wishlist_count = 0
    if request.user.is_authenticated:
        wishlist_count = Wishlist.objects.filter(user=request.user).count()
    return {
        'nav_categories': Category.objects.filter(parent__isnull=True, is_active=True).prefetch_related('children')[:10],
        'wishlist_count': wishlist_count,
    }
