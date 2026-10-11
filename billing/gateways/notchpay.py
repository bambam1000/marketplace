"""Notch Pay (https://developer.notchpay.co) : Mobile Money (MTN, Orange), cartes.

- Authentification : en-tête Authorization = clé publique du vendeur (pk_test_… en mode test).
- POST /payments → authorization_url (page de paiement Notch Pay) ; GET /payments/{référence} → état.
- Notifications : en-tête x-notch-signature = HMAC-SHA256 du corps brut avec la « clé de hachage » du webhook.
"""
import hashlib
import hmac
import re
from decimal import Decimal, InvalidOperation

from config import secretbox

from .base import Gateway, GatewayError, PaymentResult, call

BASE_URL = 'https://api.notchpay.co'
STATUSES = {'complete': 'paid', 'completed': 'paid', 'success': 'paid', 'failed': 'failed', 'canceled': 'cancelled',
            'cancelled': 'cancelled', 'expired': 'expired', 'pending': 'pending', 'processing': 'pending'}


class NotchPay(Gateway):
    label = 'Notch Pay'

    def _key(self, public_key=None):
        key = public_key or secretbox.decrypt(self.gateway.public_key_enc)
        if not key:
            raise GatewayError('Clé Notch Pay absente : reconnectez votre compte.')
        return key

    @staticmethod
    def _message(data, status):
        msg = data.get('message') or data.get('error') or ''
        if isinstance(msg, dict):
            msg = ' '.join(str(v) for v in msg.values())
        return str(msg)[:200] or f'erreur HTTP {status}'

    def check_keys(self, public_key):
        status, data = call('GET', f'{BASE_URL}/payments?limit=1', {'Authorization': public_key})
        if status in (401, 403):
            raise GatewayError('Clé refusée par Notch Pay : vérifiez que vous avez copié la clé publique (pk_…).', status)
        if status >= 400:
            raise GatewayError(f'Notch Pay : {self._message(data, status)}', status)

    def create(self, payment, customer, callback_url, description, cancel_url=''):
        phone = re.sub(r'\D', '', customer.get('phone') or '')
        body = {
            'amount': int(payment.amount), 'currency': payment.currency, 'reference': payment.reference,
            'callback': callback_url, 'description': description[:250],
            'customer': {k: v for k, v in {'name': (customer.get('name') or '')[:100], 'email': customer.get('email') or '',
                                           'phone': phone}.items() if v},
        }
        if customer.get('email'):
            body['email'] = customer['email']
        if phone:
            body['phone'] = phone
        status, data = call('POST', f'{BASE_URL}/payments', {'Authorization': self._key()}, body)
        if status in (401, 403):
            raise GatewayError('La boutique a un problème avec son compte de paiement. Choisissez un autre moyen ou réessayez plus tard.', status)
        url = data.get('authorization_url')
        if status >= 400 or not url:
            raise GatewayError(f'Paiement impossible : {self._message(data, status)}', status)
        tx = data.get('transaction')
        ref = tx.get('reference') or tx.get('id') if isinstance(tx, dict) else tx
        return url, str(ref or '')[:100]

    def retrieve(self, payment):
        last = None
        for ref in dict.fromkeys(r for r in (payment.reference, payment.provider_ref) if r):
            status, data = call('GET', f'{BASE_URL}/payments/{ref}', {'Authorization': self._key()})
            if status == 404:
                last = GatewayError('Paiement introuvable chez Notch Pay.', 404)
                continue
            if status >= 400:
                raise GatewayError(f'Notch Pay : {self._message(data, status)}', status)
            tx = data.get('transaction') if isinstance(data.get('transaction'), dict) else data
            try:
                amount = Decimal(str(tx.get('amount')))
            except (InvalidOperation, TypeError):
                amount = None
            channel = tx.get('payment_method') or tx.get('channel') or ''
            return PaymentResult(
                status=STATUSES.get(str(tx.get('status', '')).lower(), 'pending'), amount=amount,
                currency=str(tx.get('currency') or '').upper(), provider_ref=str(tx.get('reference') or tx.get('id') or '')[:100],
                channel=(channel if isinstance(channel, str) else '')[:50], raw=tx,
            )
        raise last or GatewayError('Paiement introuvable chez Notch Pay.')

    def verify_webhook(self, raw_body, headers):
        secret = secretbox.decrypt(self.gateway.webhook_secret_enc)
        signature = headers.get('x-notch-signature') or ''
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip())

    @staticmethod
    def webhook_references(payload):
        data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        refs = [data.get(k) for k in ('merchant_reference', 'reference', 'trxref', 'id')]
        tx = data.get('transaction')
        if isinstance(tx, dict):
            refs += [tx.get(k) for k in ('merchant_reference', 'reference', 'id')]
        return [str(r) for r in refs if r]
