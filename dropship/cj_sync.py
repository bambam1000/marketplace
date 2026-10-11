"""Connexion directe CJ : import de produits, mise à jour des prix et stocks, transmission
des commandes et récupération du suivi."""
import html
import re
import time

from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from .cj import TOKEN_ERRORS, CJClient, CJError
from .models import SupplierOrder, SupplierProduct, SupplierVariant

MAX_STOCK = 99_999

SIZE_RE = re.compile(
    r'^(xxs|xs|s|m|l|xl|xxl|xxxl|[2-6]xl|one ?size|free ?size|\d{1,3}(?:[.,]\d)?'
    r'|(?:eu|us|uk|cn)\s?\d{1,3}(?:[.,]\d)?|\d+(?:[.,]\d+)?\s?(?:cm|mm|m|ml|l|g|kg|pcs?|inch|in|"))$', re.I)

# Régions du Cameroun (CJ demande une « province ») ; à défaut on reprend la ville
REGIONS = {
    'douala': 'Littoral', 'edea': 'Littoral', 'nkongsamba': 'Littoral', 'yaounde': 'Centre', 'mbalmayo': 'Centre',
    'obala': 'Centre', 'bafoussam': 'Ouest', 'dschang': 'Ouest', 'mbouda': 'Ouest', 'foumban': 'Ouest',
    'bamenda': 'North-West', 'kumbo': 'North-West', 'buea': 'South-West', 'limbe': 'South-West',
    'kumba': 'South-West', 'garoua': 'North', 'maroua': 'Far North', 'ngaoundere': 'Adamawa',
    'bertoua': 'East', 'ebolowa': 'South', 'kribi': 'South', 'sangmelima': 'South',
}


def split_variant(key):
    """« Black-XL » → ('Black', 'XL'). Une valeur non reconnue comme taille est une couleur."""
    parts = [p.strip() for p in re.split(r'\s*[-/|;]\s*', key or '') if p.strip()]
    sizes = [p for p in parts if SIZE_RE.match(p)]
    others = [p for p in parts if not SIZE_RE.match(p)]
    return (' '.join(others)[:100], (sizes[0] if sizes else '')[:100])


def clean_description(text, fallback):
    text = re.sub(r'(?i)<\s*(br|/p|/div|/li|/h\d)\s*/?>', '\n', text or '')
    text = html.unescape(strip_tags(text))
    lines = [re.sub(r'[ \t\xa0]+', ' ', l).strip() for l in text.splitlines()]
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    return text[:5000] or fallback


def _region(city):
    import unicodedata
    norm = unicodedata.normalize('NFKD', city or '').encode('ascii', 'ignore').decode().lower().strip()
    return REGIONS.get(norm, (city or 'Littoral')[:50])


def _shipping_for(client, conn, vid):
    """Port par article pour le mode de livraison choisi (ou le moins cher). (prix, nom, erreur)"""
    try:
        options = client.freight(vid, conn.ship_to_country)
    except CJError as exc:
        return None, '', str(exc)
    if not options:
        return None, '', f'CJ ne livre pas ce produit vers {conn.ship_to_country}.'
    wanted = (conn.logistic_name or '').strip().lower()
    chosen = next((o for o in options if o['name'].lower() == wanted), options[0])
    return chosen['price'], chosen['name'], ''


def _apply_variants(sp, variants):
    """Variantes CJ enregistrées ; les couleurs et tailles proposées au client en découlent."""
    keep = []
    for v in variants:
        color, size = split_variant(v['key'])
        if not color and not size:
            color = v['key'][:100]
        obj, _ = SupplierVariant.objects.update_or_create(item=sp, vid=v['vid'], defaults={
            'sku': v['sku'][:120], 'label': v['key'][:200], 'color': color, 'size': size,
            'cost': v['cost'], 'stock': min(v['stock'], MAX_STOCK),
        })
        keep.append(obj.pk)
    sp.variants.exclude(pk__in=keep).delete()
    product = sp.product
    colors = _distinct(sp.variants.values_list('color', flat=True))
    sizes = _distinct(sp.variants.values_list('size', flat=True))
    if variants and (product.colors, product.sizes) != (colors, sizes):
        product.colors, product.sizes = colors, sizes
        product.save(update_fields=['colors', 'sizes'])


def _distinct(values):
    out = []
    for v in values:
        if v and v.lower() not in [o.lower() for o in out]:
            out.append(v)
    return ', '.join(out)[:300]


def _set_stock(product, stock, conn, user, reason):
    delta = stock - product.stock
    if delta:
        product.adjust_stock(delta, 'adjustment' if product.stock else 'in', user=user, reason=reason,
                             reference=conn.name[:50], warehouse=conn.warehouse)


def import_product(conn, pid, category, publish, user, client=None):
    """Crée (ou met à jour) un produit Comptoir à partir d'un produit CJ. Retourne (sp, créé, avertissement)."""
    from catalog.models import Product
    from inventory.models import ProductStock
    client = client or CJClient(conn)
    data = client.product(pid)
    variants = data['variants']
    cost = max([v['cost'] for v in variants] + [data['cost']])
    stock = min(sum(v['stock'] for v in variants) if variants else 0, MAX_STOCK)
    warning = ''
    shipping = None
    if conn.include_shipping and variants:
        shipping, _, warning = _shipping_for(client, conn, variants[0]['vid'])
    sku = (data['sku'] or data['pid'])[:120]
    sp = (conn.items.filter(supplier_pid=data['pid']).select_related('product').first()
          or conn.items.filter(supplier_sku=sku).select_related('product').first())
    now = timezone.now()
    with transaction.atomic():
        if sp:
            created = False
            sp.cost, sp.supplier_stock, sp.last_synced_at, sp.supplier_pid = cost, stock, now, data['pid']
            if shipping is not None:
                sp.shipping_cost = shipping
            sp.save()
            product = sp.product
            product.cost_price = conn.landed_cost(sp.total_cost)
            fields = ['cost_price']
            if sp.auto_price:
                product.price = conn.sale_price(sp.total_cost)
                fields.append('price')
            product.save(update_fields=fields)
        else:
            created = True
            total = cost + (shipping or 0)
            product = Product.objects.create(
                store=conn.warehouse.store, category=category, name=(data['name'] or sku)[:500],
                description=clean_description(data['description'], data['name']),
                price=conn.sale_price(total), cost_price=conn.landed_cost(total), stock=0, sku=sku[:50],
                weight=f'{data["weight"]} g'[:50] if data['weight'] else '',
                origin=conn.country[:100] or 'Chine', is_active=publish,
            )
            sp = SupplierProduct.objects.create(
                connection=conn, product=product, supplier_sku=sku, supplier_pid=data['pid'], cost=cost,
                shipping_cost=shipping or 0, supplier_stock=stock, image_urls=data['images'][:5], last_synced_at=now,
            )
            ProductStock.objects.get_or_create(product=product, warehouse=conn.warehouse)
        _apply_variants(sp, variants)
        _set_stock(product, stock, conn, user, 'Stock CJ Dropshipping')
    return sp, created, warning


def sync_items(conn, items, user=None, budget_seconds=25):
    """Met à jour coût, variantes et stock des produits CJ, dans la limite de temps. (mis à jour, restants, erreurs)"""
    client = CJClient(conn)
    start = time.monotonic()
    done, errors = 0, []
    items = [sp for sp in items if sp.supplier_pid]
    for i, sp in enumerate(items):
        if time.monotonic() - start > budget_seconds:
            return done, len(items) - i, errors
        try:
            data = client.product(sp.supplier_pid)
        except CJError as exc:
            if exc.code in TOKEN_ERRORS:
                raise
            errors.append(f'{sp.product.name[:50]} : {exc}')
            if exc.code is None and 'ne répond pas' in str(exc):
                return done, len(items) - i - 1, errors
            continue
        variants = data['variants']
        with transaction.atomic():
            sp.cost = max([v['cost'] for v in variants] + [data['cost']])
            sp.supplier_stock = min(sum(v['stock'] for v in variants), MAX_STOCK) if variants else 0
            sp.last_synced_at = timezone.now()
            sp.save(update_fields=['cost', 'supplier_stock', 'last_synced_at'])
            product = sp.product
            product.cost_price = conn.landed_cost(sp.total_cost)
            fields = ['cost_price']
            if sp.auto_price:
                product.price = conn.sale_price(sp.total_cost)
                fields.append('price')
            product.save(update_fields=fields)
            _apply_variants(sp, variants)
            _set_stock(product, sp.supplier_stock, conn, user, 'Synchronisation CJ Dropshipping')
        done += 1
    conn.last_sync_at = timezone.now()
    conn.save(update_fields=['last_sync_at'])
    return done, 0, errors


# ─── Commandes ───

def build_order_payload(so):
    """Commande CJ à partir de la commande fournisseur. Lève CJError si une ligne n'a pas de variante CJ."""
    conn, order = so.connection, so.order
    products, problems = {}, []
    for line in so.lines:
        sp = SupplierProduct.objects.filter(product=line.product, connection=conn).prefetch_related('variants').first()
        variant = sp.variant_for(line.color, line.size) if sp else None
        if not variant:
            choice = ' / '.join(x for x in (line.color, line.size) if x)
            problems.append(f'« {line.product.name[:50]} »' + (f' ({choice})' if choice else ''))
            continue
        products.setdefault(variant.vid, {'vid': variant.vid, 'quantity': 0, 'storeLineItemId': str(line.pk)})
        products[variant.vid]['quantity'] += line.quantity
    if problems:
        raise CJError('Variante CJ introuvable pour ' + ', '.join(problems)
                      + ' : synchronisez le produit ou transmettez la commande à la main.')
    if not products:
        raise CJError('Aucun article à commander chez CJ.')
    if len(products) > 20:
        raise CJError('CJ accepte au plus 20 articles différents par commande : transmettez-la à la main.')
    city = (order.shipping_city or '').strip()
    return {
        'orderNumber': so.reference,
        'shippingCountryCode': conn.ship_to_country,
        'shippingProvince': _region(city),
        'shippingCity': city[:50] or 'Douala',
        'shippingAddress': (order.shipping_address or '')[:500],
        'shippingCustomerName': (order.shipping_name or '')[:50],
        'shippingPhone': re.sub(r'[^\d+]', '', order.shipping_phone or '')[:20],
        'logisticName': conn.logistic_name or 'CJPacket Ordinary',
        'fromCountryCode': 'CN',
        'payType': 2 if conn.pay_from_balance else 3,
        'remark': f'Commande {order.order_number}'[:200],
        'products': list(products.values()),
    }


def send_order(so, user=None):
    """Transmet la commande à CJ. Lève CJError (le message est aussi gardé sur la commande)."""
    if not so.connection.api_ready:
        raise CJError("La connexion à CJ n'est pas configurée.")
    if not so.can_send:
        raise CJError("Cette commande ne peut pas être transmise : le client doit d'abord l'avoir payée."
                      if so.status == 'to_send' else 'Cette commande a déjà été traitée.')
    try:
        data = CJClient(so.connection).create_order(build_order_payload(so))
    except CJError as exc:
        so.last_error = str(exc)[:300]
        so.save(update_fields=['last_error', 'updated_at'])
        raise
    so.status, so.sent_at, so.synced_at = 'sent', timezone.now(), timezone.now()
    so.supplier_reference = str(data['orderId'])[:100]
    so.supplier_status = (data.get('orderStatus') or '')[:30]
    amount = data.get('orderAmount') or data.get('actualPayment')
    if amount not in (None, ''):
        from .cj import dec
        so.supplier_amount = dec(amount)
    so.last_error = ''
    so.save()
    return so


def tracking_url(number):
    return f'https://t.17track.net/fr#nums={number}' if number else ''


def refresh_order(so, user=None):
    """Récupère statut et suivi chez CJ. Retourne True si quelque chose a changé pour le client."""
    from messaging.utils import notify_store
    from .services import mark_order_shipped, notify_buyer_shipped
    if not so.supplier_reference or so.status in ('delivered', 'cancelled'):
        return False
    data = CJClient(so.connection).order(so.supplier_reference)
    status = (data.get('orderStatus') or '').upper()
    track = (data.get('trackNumber') or '').strip()
    so.supplier_status, so.synced_at, so.last_error = status[:30], timezone.now(), ''
    changed = False
    if track and so.status in ('sent', 'shipped') and so.tracking_number != track:
        first = so.status == 'sent'
        so.status = 'shipped'
        so.tracking_number, so.tracking_url = track[:100], tracking_url(track[:100])
        so.carrier = (data.get('logisticName') or so.carrier or '')[:100]
        so.shipped_at = so.shipped_at or timezone.now()
        so.save()
        mark_order_shipped(so, user)
        if first:
            notify_buyer_shipped(so)
        changed = True
    if status == 'DELIVERED' and so.status == 'shipped':
        so.status = 'delivered'
        changed = True
    elif status == 'CANCELLED' and so.status in ('sent', 'to_cancel'):
        was = so.status
        so.status = 'cancelled'
        changed = True
        if was == 'sent':
            notify_store(so.connection.warehouse.store, 'orders.manage', 'order',
                         f'{so.connection.name} a annulé la commande {so.reference}',
                         f'Commande client {so.order.order_number} : contactez le client ou commandez ailleurs.',
                         url=f'/dashboard/dropshipping/{so.connection.warehouse_id}/commandes/{so.pk}/')
    so.save()
    return changed


def cancel_at_supplier(so):
    """Supprime la commande chez CJ (possible tant qu'elle n'est pas payée chez CJ)."""
    CJClient(so.connection).delete_order(so.supplier_reference)
    so.status, so.supplier_status, so.last_error = 'cancelled', 'CANCELLED', ''
    so.save(update_fields=['status', 'supplier_status', 'last_error', 'updated_at'])


def auto_send(conn, limit=20):
    """Transmet les commandes payées en attente (option « transmission automatique »). (envoyées, erreurs)"""
    from messaging.utils import notify_store
    sent, errors = 0, []
    pending = (conn.orders.filter(status='to_send', order__is_paid=True)
               .exclude(order__status__in=('cancelled', 'refunded')).select_related('order', 'connection__warehouse'))
    for so in pending[:limit]:
        had_error = so.last_error
        try:
            send_order(so)
            sent += 1
        except CJError as exc:
            errors.append(f'{so.reference} : {exc}')
            if not had_error:  # on ne prévient qu'une fois par erreur
                notify_store(conn.warehouse.store, 'orders.manage', 'order',
                             f'{so.reference} non transmise à {conn.name}', str(exc)[:250],
                             url=f'/dashboard/dropshipping/{conn.warehouse_id}/commandes/{so.pk}/')
    return sent, errors


def refresh_open_orders(conn, budget_seconds=60):
    start = time.monotonic()
    changed, errors = 0, []
    for so in conn.orders.filter(status__in=('sent', 'shipped')).exclude(supplier_reference='').select_related('order'):
        if time.monotonic() - start > budget_seconds:
            break
        try:
            changed += refresh_order(so)
        except CJError as exc:
            if exc.code in TOKEN_ERRORS:
                raise
            errors.append(f'{so.reference} : {exc}')
    return changed, errors


def on_order_saved(order):
    """Commande payée : transmission immédiate chez les fournisseurs en transmission automatique."""
    pending = (SupplierOrder.objects.filter(order=order, status='to_send', connection__auto_send=True,
                                            connection__provider='cj')
               .exclude(connection__api_key_enc='').select_related('connection__warehouse'))
    for conn in {so.connection for so in pending}:
        try:
            auto_send(conn)
        except Exception:  # noqa: BLE001 — la commande du client ne doit jamais échouer à cause du fournisseur
            import logging
            logging.getLogger(__name__).exception('Transmission automatique CJ')
