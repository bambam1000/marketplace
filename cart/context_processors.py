from .utils import get_cart


def cart_context(request):
    cart = get_cart(request)
    count = sum(item.get('quantity', 0) for item in cart.values())
    total = sum(item.get('price', 0) * item.get('quantity', 0) for item in cart.values())
    return {'cart_count': count, 'cart_total': total}
