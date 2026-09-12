"""Gestion du panier : session pour les visiteurs, base de données pour les connectés."""
from catalog.models import Product
from .models import CartItem


def get_cart(request):
    """Retourne le panier sous forme de dict {product_id: {...}}."""
    if request.user.is_authenticated:
        cart = {}
        for item in CartItem.objects.filter(user=request.user).select_related('product', 'product__store'):
            p = item.product
            cart[str(p.id)] = {
                'name': p.name,
                'price': int(p.price),
                'quantity': item.quantity,
                'image': p.image.url if p.image else '',
                'store': p.store.name,
                'store_id': p.store.id,
                'color': item.color,
                'size': item.size,
            }
        return cart
    return request.session.get('cart', {})


def save_cart(request, cart):
    """Sauvegarde le panier (DB si connecté, session sinon)."""
    if request.user.is_authenticated:
        CartItem.objects.filter(user=request.user).delete()
        for pid, item in cart.items():
            try:
                product = Product.objects.get(pk=int(pid))
            except (Product.DoesNotExist, ValueError):
                continue
            CartItem.objects.create(
                user=request.user, product=product,
                quantity=item.get('quantity', 1),
                color=item.get('color', ''), size=item.get('size', ''),
            )
    else:
        request.session['cart'] = cart
        request.session.modified = True


def merge_session_cart(request, user=None):
    """À la connexion : fusionne le panier de session dans le panier DB."""
    user = user or getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return
    session_cart = request.session.get('cart', {})
    if not session_cart:
        return
    for pid, item in session_cart.items():
        try:
            product = Product.objects.get(pk=int(pid))
        except (Product.DoesNotExist, ValueError):
            continue
        existing = CartItem.objects.filter(
            user=user, product=product,
            color=item.get('color', ''), size=item.get('size', ''),
        ).first()
        if existing:
            existing.quantity += item.get('quantity', 1)
            existing.save()
        else:
            CartItem.objects.create(
                user=user, product=product,
                quantity=item.get('quantity', 1),
                color=item.get('color', ''), size=item.get('size', ''),
            )
    request.session['cart'] = {}
    request.session.modified = True
