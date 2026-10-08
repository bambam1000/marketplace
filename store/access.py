"""Règles d'accès communes au dashboard vendeur et à la facturation.

- Admin et propriétaire de la boutique : accès à tout.
- Employé actif : selon les permissions de son rôle (StoreRole.PERMISSION_CHOICES),
  et limité à son entrepôt s'il y est rattaché (StoreMember.warehouse).
"""


def is_admin(user):
    return user.is_superuser or user.role == 'admin'


def acting_store(user):
    """Boutique sur laquelle l'utilisateur travaille : la sienne, sinon celle où il est employé."""
    store = user.store
    if store is None:
        member = user.store_memberships.filter(is_active=True).select_related('store').first()
        store = member.store if member else None
    return store


def has_perm(user, store, permission):
    """permission=None : réservé au propriétaire et aux admins."""
    if is_admin(user) or user.stores.filter(pk=store.pk).exists():
        return True
    if permission is None:
        return False
    member = user.store_memberships.filter(store=store, is_active=True).select_related('role').first()
    return bool(member and member.has_permission(permission))


def member_warehouse_id(user, store):
    """Entrepôt auquel un employé est limité (None = pas de limite, propriétaire ou admin)."""
    if is_admin(user) or user.stores.filter(pk=store.pk).exists():
        return None
    member = user.store_memberships.filter(store=store, is_active=True).first()
    return member.warehouse_id if member else None


def stock_access(user, store, permission, warehouse=None):
    """Comme has_perm, mais un employé limité à un entrepôt n'a accès qu'à celui-là."""
    if not has_perm(user, store, permission):
        return False
    limit = member_warehouse_id(user, store)
    return limit is None or (warehouse is not None and limit == warehouse.pk)


def page_scope(user, permission):
    """Périmètre d'une page de liste.

    Retourne (autorisé, boutique, entrepôt_limite) :
    - admin : (True, None, None) — None = toutes les boutiques ;
    - propriétaire / employé autorisé : (True, boutique, id d'entrepôt ou None) ;
    - sinon : (False, boutique ou None, None).
    """
    if is_admin(user):
        return True, None, None
    store = acting_store(user)
    if store is None or not has_perm(user, store, permission):
        return False, store, None
    return True, store, member_warehouse_id(user, store)


def resolve_warehouse(request, store, limit):
    """Entrepôt demandé par ?warehouse=, validé contre la boutique.

    Retourne (entrepôt, invalide) : un employé limité est toujours ramené à son entrepôt ;
    un id inconnu, non numérique ou d'une autre boutique donne (None, True).
    """
    from inventory.models import Warehouse
    warehouses = Warehouse.objects.select_related('store', 'manager')
    if store is not None:
        warehouses = warehouses.filter(store=store)
    if limit:
        return warehouses.filter(pk=limit).first(), False
    raw = request.GET.get('warehouse', '')
    if not raw:
        return None, False
    warehouse = warehouses.filter(pk=raw).first() if raw.isdigit() else None
    return warehouse, warehouse is None


def selectable_warehouses(store, limit):
    """Entrepôts proposés dans le sélecteur d'une page."""
    from inventory.models import Warehouse
    warehouses = Warehouse.objects.filter(is_active=True)
    if store is not None:
        warehouses = warehouses.filter(store=store)
    if limit:
        warehouses = warehouses.filter(pk=limit)
    return warehouses
