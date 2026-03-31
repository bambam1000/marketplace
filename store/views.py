from django.shortcuts import render, get_object_or_404
from django.db.models import Q, Count
from .models import Store
from catalog.models import Product


def store_list(request):
    stores = Store.objects.filter(is_active=True).annotate(
        nb_products=Count('products', filter=Q(products__is_active=True))
    )
    q = request.GET.get('q', '')
    if q:
        stores = stores.filter(Q(name__icontains=q) | Q(city__icontains=q))
    verified = request.GET.get('verified')
    if verified:
        stores = stores.filter(is_verified=True)
    sort = request.GET.get('sort', '-created_at')
    sort_map = {
        '-created_at': '-created_at',
        'name': 'name',
        'products': '-nb_products',
    }
    stores = stores.order_by(sort_map.get(sort, '-created_at'))
    return render(request, 'store/list.html', {
        'stores': stores,
        'search_query': q,
        'sort': sort,
        'total': stores.count(),
        'verified_filter': verified,
    })


def store_detail(request, slug):
    store = get_object_or_404(Store, slug=slug, is_active=True)
    products = store.products.filter(is_active=True)
    sort = request.GET.get('sort', '-created_at')
    if sort == 'price':
        products = products.order_by('price')
    elif sort == '-price':
        products = products.order_by('-price')
    elif sort == 'popular':
        products = products.order_by('-orders_count')
    return render(request, 'store/detail.html', {'store': store, 'products': products})
