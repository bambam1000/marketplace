"""Annulation d'une commande : mêmes effets qu'elle vienne du client ou de la boutique."""
from django.db.models import F

STOPPED = ('cancelled', 'refunded')


def apply_cancellation(order, user, reason=''):
    """Passe la commande en « annulée » et défait ce que la commande avait produit :
    stock, compteur de ventes, points de fidélité, utilisation du code promo, crédit des vendeurs.
    À appeler dans une transaction, sur une commande verrouillée et pas encore annulée.
    Retourne {boutique: montant de ses articles}."""
    from marketing.models import LoyaltyProgram
    from accounting.models import Transaction, SellerWallet
    from .models import OrderStatusEvent

    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])
    OrderStatusEvent.objects.create(order=order, status='cancelled', by=user, note=reason[:255])

    per_store = {}
    for it in order.items.select_related('product', 'store'):
        it.product.adjust_stock(it.quantity, 'return', user=user, reason='Annulation de commande',
                                reference=order.order_number, warehouse=it.warehouse)
        type(it.product).objects.filter(pk=it.product_id, orders_count__gte=it.quantity).update(
            orders_count=F('orders_count') - it.quantity)
        per_store[it.store] = per_store.get(it.store, 0) + it.subtotal

    # Fidélité : retirer ce que la commande avait rapporté (recalcul du niveau sans e-mail)
    for st, amount in per_store.items():
        lp = LoyaltyProgram.objects.filter(user=order.buyer, store=st).first()
        if lp:
            cfg = lp.get_settings()
            pts = int(amount / cfg.points_per_amount) if cfg.points_per_amount > 0 else 0
            lp.points = max(0, lp.points - pts)
            lp.total_spent = max(0, lp.total_spent - amount)
            lp.total_orders = max(0, lp.total_orders - 1)
            lp.tier = cfg.tier_for_points(lp.points)
            lp.save()

    if order.promo_code_id:
        type(order.promo_code).objects.filter(pk=order.promo_code_id, usage_count__gt=0).update(
            usage_count=F('usage_count') - 1)

    # Vendeurs déjà crédités (commande confirmée) : retirer la vente de leur portefeuille
    for tx in Transaction.objects.filter(order=order, type='sale', status='completed'):
        wallet, _ = SellerWallet.objects.get_or_create(user=tx.user)
        wallet.balance -= tx.net_amount
        wallet.total_earned = max(0, wallet.total_earned - tx.amount)
        wallet.total_commission_paid = max(0, wallet.total_commission_paid - tx.commission_amount)
        wallet.save()
        tx.status = 'cancelled'
        tx.save(update_fields=['status'])
    return per_store
