from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from catalog.models import Product
from orders.models import Order, OrderItem
from .utils import get_cart, save_cart

@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    cart = get_cart(request)
    pid = str(product_id)
    qty = int(request.POST.get('quantity', 1))
    color = request.POST.get('color', '')
    size = request.POST.get('size', '')
    if pid in cart:
        cart[pid]['quantity'] += qty
    else:
        cart[pid] = {
            'name': product.name,
            'price': int(product.price),
            'quantity': qty,
            'image': product.image.url if product.image else '',
            'store': product.store.name,
            'store_id': product.store.id,
            'color': color,
            'size': size,
        }
    save_cart(request, cart)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'cart_count': sum(i['quantity'] for i in cart.values())})
    messages.success(request, f'"{product.name}" ajouté au panier !')
    return redirect(request.META.get('HTTP_REFERER', 'home'))

def cart_view(request):
    from store.models import Store
    cart = get_cart(request)
    items = []
    subtotal = 0
    for pid, item in cart.items():
        s = item['price'] * item['quantity']
        subtotal += s
        # Récupérer le numéro WhatsApp de la boutique
        whatsapp = ''
        if 'store_id' in item:
            try:
                store = Store.objects.get(id=item['store_id'])
                whatsapp = store.whatsapp
            except Store.DoesNotExist:
                pass
        items.append({**item, 'id': pid, 'subtotal': s, 'whatsapp': whatsapp})
    shipping = 2000 if subtotal < 50000 and subtotal > 0 else 0
    return render(request, 'cart/cart.html', {
        'cart_items': items,
        'subtotal': subtotal,
        'shipping': shipping,
        'total': subtotal + shipping,
    })

@require_POST
def update_cart(request):
    pid = request.POST.get('product_id')
    action = request.POST.get('action')
    cart = get_cart(request)
    if pid in cart:
        if action == 'increase': cart[pid]['quantity'] += 1
        elif action == 'decrease':
            cart[pid]['quantity'] -= 1
            if cart[pid]['quantity'] <= 0: del cart[pid]
        elif action == 'remove': del cart[pid]
    save_cart(request, cart)
    return redirect('cart:view')

@login_required
def checkout(request):
    cart = get_cart(request)
    if not cart:
        return redirect('cart:view')
    items = []
    subtotal = 0
    for pid, item in cart.items():
        s = item['price'] * item['quantity']
        subtotal += s
        items.append({**item, 'id': pid, 'subtotal': s})
    shipping = 2000 if subtotal < 50000 else 0
    total = subtotal + shipping

    if request.method == 'POST':
        order = Order.objects.create(
            buyer=request.user,
            payment_method=request.POST.get('payment_method', 'momo'),
            subtotal=subtotal,
            shipping_cost=shipping,
            total_amount=total,
            shipping_name=request.POST.get('shipping_name'),
            shipping_phone=request.POST.get('shipping_phone'),
            shipping_address=request.POST.get('shipping_address'),
            shipping_city=request.POST.get('shipping_city', 'Douala'),
            notes=request.POST.get('notes', ''),
        )
        for pid, item in cart.items():
            try:
                product = Product.objects.get(pk=int(pid))
                OrderItem.objects.create(
                    order=order, product=product, store=product.store,
                    quantity=item['quantity'], price=item['price'],
                    color=item.get('color', ''), size=item.get('size', ''),
                )
                product.orders_count += item['quantity']
                product.save(update_fields=['orders_count'])
                product.adjust_stock(
                    -item['quantity'], 'sale', user=request.user,
                    reason='Vente en ligne', reference=order.order_number,
                )
            except Product.DoesNotExist:
                pass
        save_cart(request, {})
        messages.success(request, f'Commande {order.order_number} créée !')
        return redirect('orders:success', order_number=order.order_number)

    return render(request, 'cart/checkout.html', {
        'cart_items': items, 'subtotal': subtotal,
        'shipping': shipping, 'total': total,
    })
