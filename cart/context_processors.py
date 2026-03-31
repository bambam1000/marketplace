def cart_context(request):
    cart = request.session.get('cart', {})
    count = sum(item.get('quantity', 0) for item in cart.values())
    total = sum(item.get('price', 0) * item.get('quantity', 0) for item in cart.values())
    return {'cart_count': count, 'cart_total': total}
