"""Liens entre le dropshipping et le reste du site : panier, commande, annulation."""
from django.db.models import F, Sum


def supplier_of(product):
    """Connexion fournisseur d'un produit, ou None (produit stocké par la boutique)."""
    from .models import SupplierProduct
    sp = (SupplierProduct.objects.filter(product=product, connection__warehouse__is_active=True)
          .select_related('connection__warehouse').first())
    return sp.connection if sp else None


def has_dropship(product_ids):
    from .models import SupplierProduct
    return SupplierProduct.objects.filter(product_id__in=product_ids).exists()


def fulfilment_warehouse(product):
    """Entrepôt qui expédie ce produit : le fournisseur pour un produit en dropshipping,
    sinon l'entrepôt principal de la boutique (jamais un entrepôt fournisseur)."""
    conn = supplier_of(product)
    if conn:
        return conn.warehouse
    from inventory.models import Warehouse
    whs = Warehouse.objects.filter(store=product.store).exclude(kind='dropship')
    return whs.filter(is_default=True).first() or whs.filter(is_active=True).first()


def create_supplier_orders(order):
    """Une commande fournisseur par entrepôt fournisseur présent dans la commande ; l'équipe est prévenue."""
    from messaging.utils import notify_store
    from .models import SupplierConnection, SupplierOrder
    created = []
    conns = SupplierConnection.objects.filter(warehouse__orderitem__order=order).distinct().select_related('warehouse__store')
    for conn in conns:
        lines = order.items.filter(warehouse=conn.warehouse)
        cost = lines.aggregate(t=Sum(F('quantity') * F('unit_cost')))['t'] or 0
        so, new = SupplierOrder.objects.get_or_create(connection=conn, order=order, defaults={'cost': cost})
        if new:
            created.append(so)
            notify_store(conn.warehouse.store, 'orders.view', 'order',
                         f'Commande {order.order_number} à faire expédier par {conn.name}',
                         "Dès que le client a payé, transmettez la commande au fournisseur depuis l'entrepôt "
                         f'{conn.warehouse.name}.',
                         url=f'/dashboard/dropshipping/{conn.warehouse_id}/commandes/{so.pk}/')
    return created


def on_order_cancelled(order):
    """Commande client annulée : rien à faire si le fournisseur n'a rien reçu,
    sinon la commande fournisseur passe « à annuler chez le fournisseur » et l'équipe est prévenue."""
    from messaging.utils import notify_store
    for so in order.supplier_orders.select_related('connection__warehouse__store'):
        if so.status == 'to_send':
            so.status = 'cancelled'
            so.save(update_fields=['status', 'updated_at'])
        elif so.status in ('sent', 'shipped'):
            so.status = 'to_cancel'
            so.save(update_fields=['status', 'updated_at'])
            notify_store(so.connection.warehouse.store, 'orders.manage', 'order',
                         f'Commande {order.order_number} annulée : prévenez {so.connection.name}',
                         f'La commande avait déjà été transmise au fournisseur ({so.reference}). '
                         "Demandez-lui l'annulation ou le remboursement.",
                         url=f'/dashboard/dropshipping/{so.connection.warehouse_id}/commandes/{so.pk}/')


def notify_buyer_shipped(so):
    from messaging.utils import notify
    order = so.order
    track = f' Numéro de suivi : {so.tracking_number}' + (f' ({so.carrier})' if so.carrier else '') + '.' if so.tracking_number else ''
    notify(order.buyer, 'order', f'Commande {order.order_number} : colis expédié',
           f'Une partie de votre commande a été expédiée par notre fournisseur partenaire.{track} '
           f'Livraison estimée sous {so.connection.delivery_min_days} à {so.connection.delivery_max_days} jours.',
           url=f'/commandes/{order.order_number}/', send_email=True)


def mark_order_shipped(so, user):
    """Toute la commande vient de ce fournisseur : elle passe « expédiée » côté client aussi."""
    from orders.models import OrderStatusEvent
    order = so.order
    if not order.tracking_number:
        order.tracking_number = so.tracking_number
        order.save(update_fields=['tracking_number', 'updated_at'])
    if order.items.exclude(warehouse=so.connection.warehouse).exists():
        return
    if order.status in ('confirmed', 'processing'):
        order.status = 'shipped'
        order.save(update_fields=['status', 'updated_at'])
        OrderStatusEvent.objects.create(order=order, status='shipped', by=user,
                                        note=f'Expédiée par le fournisseur · suivi {so.tracking_number}'[:255])
