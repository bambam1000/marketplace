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

def _get_applied_promo(request, cart):
    """Retourne (promo, discount) depuis la session, en vérifiant la validité.
    La réduction s'applique sur le sous-total des produits de la boutique du code."""
    from marketing.models import PromoCode
    code = request.session.get('promo_code')
    if not code or not cart:
        return None, 0
    try:
        promo = PromoCode.objects.get(code=code)
    except PromoCode.DoesNotExist:
        request.session.pop('promo_code', None)
        return None, 0
    if not promo.is_valid:
        request.session.pop('promo_code', None)
        return None, 0
    # Sous-total des articles de la boutique du code
    store_subtotal = sum(
        item['price'] * item['quantity']
        for item in cart.values()
        if item.get('store_id') == promo.store_id
    )
    if store_subtotal <= 0:
        request.session.pop('promo_code', None)
        return None, 0
    discount = promo.calculate_discount(store_subtotal)
    if discount <= 0:
        return promo, 0
    return promo, discount


def _get_loyalty_discount(request, cart, promo=None):
    """Remise fidélité automatique par boutique (non cumulable avec un code promo).
    Retourne (montant_total, détails=[(store_name, tier_label, rate, amount)])."""
    if promo or not request.user.is_authenticated or not cart:
        return 0, []
    from marketing.models import LoyaltyProgram
    from collections import defaultdict
    store_subtotals = defaultdict(int)
    for item in cart.values():
        sid = item.get('store_id')
        if sid:
            store_subtotals[sid] += item['price'] * item['quantity']
    if not store_subtotals:
        return 0, []
    total = 0
    details = []
    memberships = {
        m.store_id: m
        for m in LoyaltyProgram.objects.filter(user=request.user, store_id__in=store_subtotals.keys()).select_related('store')
    }
    tier_labels = {'silver': 'Argent', 'gold': 'Or', 'platinum': 'Platine'}
    for sid, subtotal in store_subtotals.items():
        m = memberships.get(sid)
        if not m:
            continue
        rate = m.discount_rate
        if rate > 0:
            amount = int(subtotal * rate / 100)
            total += amount
            details.append((m.store.name, tier_labels.get(m.tier, m.tier), rate, amount))
    return total, details


@require_POST
def apply_promo(request):
    """Applique un code promo au panier."""
    from marketing.models import PromoCode
    code = request.POST.get('promo_code', '').strip().upper()
    if not code:
        messages.error(request, 'Entrez un code promo.')
        return redirect('cart:view')
    cart = get_cart(request)
    try:
        promo = PromoCode.objects.get(code=code)
    except PromoCode.DoesNotExist:
        messages.error(request, f'Code « {code} » invalide.')
        return redirect('cart:view')
    if not promo.is_valid:
        messages.error(request, 'Ce code promo est expiré ou épuisé.')
        return redirect('cart:view')
    # Le code doit concerner une boutique présente dans le panier
    store_ids = {item.get('store_id') for item in cart.values()}
    if promo.store_id not in store_ids:
        messages.error(request, 'Ce code ne s\'applique pas aux produits de votre panier.')
        return redirect('cart:view')
    store_subtotal = sum(
        item['price'] * item['quantity']
        for item in cart.values() if item.get('store_id') == promo.store_id
    )
    if store_subtotal < promo.min_purchase_amount:
        messages.error(request, f'Montant minimum d\'achat pour ce code : {promo.min_purchase_amount:.0f} FCFA.')
        return redirect('cart:view')
    request.session['promo_code'] = code
    messages.success(request, f'Code « {code} » appliqué !')
    return redirect('cart:view')


@require_POST
def remove_promo(request):
    """Retire le code promo du panier."""
    request.session.pop('promo_code', None)
    messages.info(request, 'Code promo retiré.')
    return redirect('cart:view')


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
    promo, discount = _get_applied_promo(request, cart)
    loyalty_discount, loyalty_details = _get_loyalty_discount(request, cart, promo)
    return render(request, 'cart/cart.html', {
        'cart_items': items,
        'subtotal': subtotal,
        'shipping': shipping,
        'promo': promo,
        'discount': discount,
        'loyalty_discount': loyalty_discount,
        'loyalty_details': loyalty_details,
        'total': subtotal + shipping - discount - loyalty_discount,
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
    promo, discount = _get_applied_promo(request, cart)
    loyalty_discount, loyalty_details = _get_loyalty_discount(request, cart, promo)
    total = subtotal + shipping - discount - loyalty_discount

    if request.method == 'POST':
        order = Order.objects.create(
            buyer=request.user,
            payment_method=request.POST.get('payment_method', 'momo'),
            subtotal=subtotal,
            shipping_cost=shipping,
            promo_code=promo,
            discount_amount=discount,
            loyalty_discount=loyalty_discount,
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
                from inventory.models import Warehouse
                wh = Warehouse.objects.filter(store=product.store, is_default=True).first()
                OrderItem.objects.create(
                    order=order, product=product, store=product.store, warehouse=wh,
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
        if promo:
            promo.apply_code()
            # Conversion des campagnes liées à ce code promo
            from marketing.models import Campaign
            for camp in Campaign.objects.filter(promo_code=promo, status='active'):
                Campaign.objects.filter(pk=camp.pk).update(
                    conversions_count=camp.conversions_count + 1,
                    revenue_generated=camp.revenue_generated + order.total_amount,
                )
        request.session.pop('promo_code', None)

        # Points de fidélité : 1 point par tranche de 1000 FCFA, par boutique
        from marketing.models import LoyaltyProgram
        from collections import defaultdict
        store_totals = defaultdict(int)
        for item in order.items.select_related('store'):
            store_totals[item.store] += item.price * item.quantity
        for st, amount in store_totals.items():
            lp, _ = LoyaltyProgram.objects.get_or_create(user=request.user, store=st)
            lp.total_spent += amount
            lp.total_orders += 1
            lp.add_points(amount)

        save_cart(request, {})

        # Notifications : client (email) + vendeurs concernés
        from messaging.utils import notify
        notify(
            request.user, 'order',
            f'Commande {order.order_number} confirmée',
            f'Votre commande de {order.total_amount:.0f} FCFA a été enregistrée. Nous vous tiendrons informé de son avancement.',
            url=f'/commandes/{order.order_number}/',
            send_email=True,
            email_subject=f'Confirmation de commande {order.order_number} — AfriMarket',
        )
        for st in store_totals.keys():
            notify(
                st.owner, 'order',
                f'Nouvelle commande {order.order_number}',
                f'{request.user.display_name} a commandé pour {order.total_amount:.0f} FCFA.',
                url=f'/dashboard/commandes/{order.order_number}/',
                send_email=True,
            )

        messages.success(request, f'Commande {order.order_number} créée !')
        return redirect('orders:success', order_number=order.order_number)

    return render(request, 'cart/checkout.html', {
        'cart_items': items, 'subtotal': subtotal,
        'shipping': shipping, 'promo': promo, 'discount': discount,
        'loyalty_discount': loyalty_discount, 'loyalty_details': loyalty_details,
        'total': total,
    })
