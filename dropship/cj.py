"""Client de l'API CJ Dropshipping 2.0 (https://developers.cjdropshipping.com).

- Authentification : la clé d'API donne un jeton d'accès (en-tête CJ-Access-Token) et un jeton de
  renouvellement ; ils sont chiffrés dans SupplierConnection et renouvelés automatiquement.
- CJ limite à 1 requête par seconde : les appels sont espacés (CJ_MIN_INTERVAL).
- Toutes les réponses ont la forme {code, result, message, data} ; une erreur lève CJError
  avec un message lisible par le vendeur.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from . import secret

BASE_URL = 'https://developers.cjdropshipping.com/api2.0/v1/'
TIMEOUT = 20
# Codes CJ signalant un jeton invalide ou expiré
TOKEN_ERRORS = {1600001, 1600003, 1600100, 1600101}


class CJError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


_lock = threading.Lock()
_last_call = [0.0]


def _throttle():
    interval = getattr(settings, 'CJ_MIN_INTERVAL', 1.1)
    with _lock:
        wait = _last_call[0] + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _transport(method, url, headers, body):
    """Appel HTTP brut → (statut, octets). Remplacé dans les tests."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200_000)


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%d %H:%M:%S'):
        try:
            dt = datetime.strptime(str(value), fmt)
            return dt if dt.tzinfo else timezone.make_aware(dt, timezone.utc)
        except ValueError:
            continue
    return None


def dec(value, default='0'):
    """Prix CJ (nombre, texte, ou fourchette « 1.20-3.50 » → borne haute)."""
    if value in (None, ''):
        return Decimal(default)
    text = str(value).replace(',', '.').split('-')[-1].strip()
    try:
        return Decimal(text).quantize(Decimal('0.01'))
    except InvalidOperation:
        return Decimal(default)


class CJClient:
    def __init__(self, connection):
        self.conn = connection

    # ─── HTTP ───

    def _call(self, method, path, params=None, body=None, auth=True, retry=True):
        url = BASE_URL + path
        if params:
            url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if auth:
            headers['CJ-Access-Token'] = self.access_token()
        payload = json.dumps(body).encode() if body is not None else None
        _throttle()
        try:
            status, raw = _transport(method, url, headers, payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CJError(f'CJ ne répond pas ({getattr(exc, "reason", exc)}). Réessayez dans un instant.')
        try:
            data = json.loads(raw or b'{}')
        except ValueError:
            raise CJError(f'Réponse illisible de CJ (HTTP {status}).')
        code = data.get('code')
        if status == 401 or code in TOKEN_ERRORS:
            if auth and retry:
                self.authenticate(force=True)
                return self._call(method, path, params, body, auth, retry=False)
            raise CJError('Clé d\'API CJ refusée : vérifiez-la dans les réglages.', code)
        if status >= 400 or code not in (200, None) or data.get('result') is False:
            raise CJError(f'CJ : {data.get("message") or f"erreur HTTP {status}"}', code)
        return data.get('data')

    # ─── Jetons ───

    def access_token(self):
        conn = self.conn
        token = secret.decrypt(conn.access_token_enc)
        soon = timezone.now() + timedelta(hours=1)
        if token and conn.token_expires_at and conn.token_expires_at > soon:
            return token
        refresh = secret.decrypt(conn.refresh_token_enc)
        if refresh and conn.refresh_expires_at and conn.refresh_expires_at > soon:
            try:
                data = self._call('POST', 'authentication/refreshAccessToken', body={'refreshToken': refresh},
                                  auth=False, retry=False)
                self._store_tokens(data)
                return secret.decrypt(conn.access_token_enc)
            except CJError:
                pass
        self.authenticate(force=True)
        return secret.decrypt(conn.access_token_enc)

    def authenticate(self, api_key=None, force=False):
        """Échange la clé d'API contre des jetons. Lève CJError si la clé est refusée."""
        conn = self.conn
        key = api_key or secret.decrypt(conn.api_key_enc)
        if not key:
            raise CJError("Aucune clé d'API CJ enregistrée (ou elle doit être ressaisie).")
        data = self._call('POST', 'authentication/getAccessToken', body={'apiKey': key}, auth=False, retry=False)
        if not data or not data.get('accessToken'):
            raise CJError("CJ n'a pas renvoyé de jeton : vérifiez la clé d'API.")
        self._store_tokens(data)
        return data

    def _store_tokens(self, data):
        conn = self.conn
        now = timezone.now()
        conn.access_token_enc = secret.encrypt(data['accessToken'])
        conn.token_expires_at = _parse_date(data.get('accessTokenExpiryDate')) or now + timedelta(days=14)
        if data.get('refreshToken'):
            conn.refresh_token_enc = secret.encrypt(data['refreshToken'])
            conn.refresh_expires_at = _parse_date(data.get('refreshTokenExpiryDate')) or now + timedelta(days=150)
        conn.api_connected_at = conn.api_connected_at or now
        conn.api_error = ''
        if conn.pk:
            conn.save(update_fields=['access_token_enc', 'token_expires_at', 'refresh_token_enc', 'refresh_expires_at',
                                     'api_connected_at', 'api_error'])

    # ─── Catalogue ───

    def search(self, keyword='', page=1, size=20):
        data = self._call('GET', 'product/listV2', params={'keyWord': keyword, 'page': page, 'size': size}) or {}
        items = []
        for block in data.get('content') or []:
            for p in block.get('productList') or []:
                items.append({
                    'pid': str(p.get('id') or ''), 'sku': p.get('sku') or '', 'name': p.get('nameEn') or '',
                    'image': p.get('bigImage') or '', 'cost': dec(p.get('nowPrice') or p.get('sellPrice')),
                    'category': p.get('threeCategoryName') or '', 'stock': int(p.get('warehouseInventoryNum') or 0),
                })
        return {'items': [i for i in items if i['pid']], 'total': int(data.get('totalRecords') or 0),
                'pages': int(data.get('totalPages') or 0)}

    def product(self, pid, origin='CN'):
        p = self._call('GET', 'product/query', params={'pid': pid})
        if not p:
            raise CJError('Produit introuvable chez CJ.')
        images = p.get('productImageSet') or []
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except ValueError:
                images = [images]
        variants = []
        for v in p.get('variants') or []:
            inv = v.get('inventories') or []
            # Les colis partent de Chine (fromCountryCode CN) : le stock des entrepôts CJ ailleurs ne compte pas
            local = [i for i in inv if (i.get('countryCode') or 'CN').upper() == origin]
            stock = sum(int(i.get('totalInventory') or 0) for i in local)
            variants.append({
                'vid': str(v.get('vid') or ''), 'sku': v.get('variantSku') or '',
                'key': v.get('variantKey') or v.get('variantNameEn') or '', 'cost': dec(v.get('variantSellPrice')),
                'image': v.get('variantImage') or '', 'stock': stock, 'weight': v.get('variantWeight'),
            })
        return {
            'pid': str(p.get('pid') or pid), 'sku': p.get('productSku') or '', 'name': p.get('productNameEn') or '',
            'description': p.get('description') or '', 'category': p.get('categoryName') or '',
            'cost': dec(p.get('sellPrice')), 'weight': p.get('productWeight'),
            'images': [u for u in [p.get('bigImage')] + list(images) if u],
            'variants': [v for v in variants if v['vid']],
        }

    def freight(self, vid, country, quantity=1, origin='CN'):
        """Options de livraison [{name, price, days}] du moins cher au plus cher."""
        data = self._call('POST', 'logistic/freightCalculate', body={
            'startCountryCode': origin, 'endCountryCode': country, 'products': [{'quantity': quantity, 'vid': vid}],
        }) or []
        options = [{'name': o.get('logisticName') or '', 'price': dec(o.get('logisticPrice')),
                    'days': o.get('logisticAging') or ''} for o in data]
        return sorted([o for o in options if o['name']], key=lambda o: o['price'])

    # ─── Commandes ───

    def create_order(self, payload):
        data = self._call('POST', 'shopping/order/createOrderV2', body=payload) or {}
        if not data.get('orderId'):
            raise CJError("CJ n'a pas renvoyé de numéro de commande.")
        return data

    def order(self, order_id):
        return self._call('GET', 'shopping/order/getOrderDetail', params={'orderId': order_id}) or {}

    def delete_order(self, order_id):
        return self._call('DELETE', 'shopping/order/deleteOrder', params={'orderId': order_id})
