"""Données du menu du dashboard : droits de l'utilisateur et compteurs (badges).

Calculées à la demande (SimpleLazyObject) : aucune requête tant qu'un template n'utilise pas `dash_nav`,
donc rien n'est coûté aux pages publiques du site.
"""
from django.db.models import F, Sum
from django.utils.functional import SimpleLazyObject

from store import access

PERMS = {
    'orders': 'orders.view', 'manage': 'orders.manage', 'sales': 'sales.view', 'sell': 'sales.create',
    'products': 'products.view', 'stock': 'stock.view', 'transfer': 'stock.transfer',
    'invoices': 'invoicing.view', 'customers': 'customers.view', 'finances': 'finances.view',
    'pos': 'pos.use', 'pos_manage': 'pos.manage',
}


# Pages rattachées aux entrepôts : le lien « Entrepôts » de la sidebar reste actif dessus
HUB_URLS = ('orders', 'order_detail', 'sales', 'sale_create', 'products', 'product_add', 'product_edit', 'inventory',
            'stock_adjust')


def _build(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    is_admin = access.is_admin(user)
    store = access.acting_store(user)
    if store is None and not is_admin:
        return {}
    can = {key: is_admin or (store is not None and access.has_perm(user, store, perm)) for key, perm in PERMS.items()}
    owner = is_admin or (store is not None and access.has_perm(user, store, None))
    can['owner'] = owner
    can['pos'] = can['pos'] or can['pos_manage']
    # Commandes, ventes, caisse, factures, produits et stock se gèrent depuis la page de chaque entrepôt
    can['warehouses'] = any(can[k] for k in ('stock', 'orders', 'sales', 'pos', 'invoices', 'products', 'finances'))
    limit = access.member_warehouse_id(user, store) if store is not None else None

    if is_admin and store is None:
        role = 'Administrateur'
    elif user.stores.filter(pk=store.pk).exists():
        role = 'Propriétaire'
    else:
        member = user.store_memberships.filter(store=store, is_active=True).select_related('role').first()
        role = member.role.name if member and member.role else 'Employé'

    badges = {}
    if store is not None:
        from orders.models import OrderItem, RFQ
        if can['orders']:
            mine = OrderItem.objects.filter(store=store, order__status='pending')
            if limit:
                mine = mine.filter(warehouse_id=limit)
            badges['orders'] = mine.values('order').distinct().count()
            badges['rfqs'] = (RFQ.objects.filter(status__in=('open', 'quoted')).exclude(quotes__store=store)
                              .exclude(buyer=store.owner).count())
        if can['stock']:
            from inventory.models import ProductStock
            out = ProductStock.objects.filter(warehouse__store=store, product__is_active=True, quantity=0)
            badges['stock'] = (out.filter(warehouse_id=limit) if limit else out).count()
        if can['pos']:
            from pos.models import POSSession
            badges['pos_open'] = POSSession.objects.filter(store=store, cashier=user, status='open').exists()
        if can['manage']:
            from whatsapp.models import WhatsAppChat
            badges['whatsapp'] = (WhatsAppChat.objects.filter(instance__store=store)
                                  .aggregate(t=Sum('unread_count'))['t'] or 0)
    return {'store': store, 'is_admin': is_admin, 'is_seller': user.is_seller, 'can': can, 'role': role, 'hub_urls': HUB_URLS,
            'badges': badges, 'initials': ''.join(w[0] for w in (user.display_name or '?').split()[:2]).upper()}


def dash_nav(request):
    return {'dash_nav': SimpleLazyObject(lambda: _build(request))}
