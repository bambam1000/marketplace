"""Stripe Checkout (https://docs.stripe.com/api/checkout/sessions) : cartes internationales, Apple Pay, Google Pay.

Pour les vendeurs installés là où Stripe est ouvert (Europe, Amérique du Nord…) et leurs clients à l'étranger.
- Authentification : Authorization: Bearer <clé secrète sk_… ou clé restreinte rk_…>, corps en x-www-form-urlencoded.
- POST /v1/checkout/sessions → url (page Stripe) ; GET /v1/checkout/sessions/{id} → payment_status, amount_total.
- Le franc CFA (XAF) est une devise sans décimales chez Stripe : le montant s'envoie tel quel.
- Notifications : en-tête Stripe-Signature « t=…,v1=… » = HMAC-SHA256 de « t.corps » avec le secret whsec_….
"""
import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation

from config import secretbox

from .base import Gateway, GatewayError, PaymentResult, call

BASE_URL = 'https://api.stripe.com/v1'
TOLERANCE = 300  # secondes acceptées entre la signature et la réception


class Stripe(Gateway):
    label = 'Stripe'
    test_amount = 1000  # Stripe refuse les paiements sous l'équivalent de 0,50 $

    def _headers(self, key=None):
        key = key or secretbox.decrypt(self.gateway.public_key_enc)
        if not key:
            raise GatewayError('Clé Stripe absente : reconnectez votre compte.')
        return {'Authorization': f'Bearer {key}'}

    @staticmethod
    def _message(data, status):
        err = data.get('error') if isinstance(data.get('error'), dict) else {}
        return str(err.get('message') or f'erreur HTTP {status}')[:200]

    def check_keys(self, secret_key):
        status, data = call('GET', f'{BASE_URL}/balance', self._headers(secret_key))
        if status in (401, 403):
            raise GatewayError('Clé refusée par Stripe : copiez la clé secrète (sk_…) de votre compte.', status)
        if status >= 400:
            raise GatewayError(f'Stripe : {self._message(data, status)}', status)

    def create(self, payment, customer, callback_url, description, cancel_url=''):
        sep = '&' if '?' in callback_url else '?'
        form = [
            ('mode', 'payment'),
            ('success_url', f'{callback_url}{sep}reference={payment.reference}'),
            ('cancel_url', cancel_url or f'{callback_url}{sep}reference={payment.reference}'),
            ('client_reference_id', payment.reference),
            ('metadata[reference]', payment.reference),
            ('payment_intent_data[metadata][reference]', payment.reference),
            ('line_items[0][quantity]', '1'),
            ('line_items[0][price_data][currency]', payment.currency.lower()),
            ('line_items[0][price_data][unit_amount]', str(int(payment.amount))),
            ('line_items[0][price_data][product_data][name]', description[:250]),
        ]
        if customer.get('email'):
            form.append(('customer_email', customer['email']))
        status, data = call('POST', f'{BASE_URL}/checkout/sessions', self._headers(), form=form)
        if status in (401, 403):
            raise GatewayError('La boutique a un problème avec son compte Stripe. Choisissez un autre moyen ou réessayez plus tard.', status)
        if status >= 400 or not data.get('url'):
            raise GatewayError(f'Paiement impossible : {self._message(data, status)}', status)
        return data['url'], str(data.get('id') or '')[:100]

    def retrieve(self, payment):
        if not payment.provider_ref:
            raise GatewayError('Session Stripe inconnue pour ce paiement.')
        status, data = call('GET', f'{BASE_URL}/checkout/sessions/{payment.provider_ref}', self._headers())
        if status == 404:
            raise GatewayError('Paiement introuvable chez Stripe.', 404)
        if status >= 400:
            raise GatewayError(f'Stripe : {self._message(data, status)}', status)
        if data.get('payment_status') == 'paid':
            state = 'paid'
        elif data.get('status') == 'expired':
            state = 'expired'
        else:
            state = 'pending'
        try:
            amount = Decimal(str(data.get('amount_total')))
        except (InvalidOperation, TypeError):
            amount = None
        return PaymentResult(status=state, amount=amount, currency=str(data.get('currency') or '').upper(),
                             provider_ref=str(data.get('id') or '')[:100], channel='Carte (Stripe)',
                             raw={k: data.get(k) for k in ('id', 'status', 'payment_status', 'amount_total', 'currency', 'payment_intent')})

    def verify_webhook(self, raw_body, headers):
        secret = secretbox.decrypt(self.gateway.webhook_secret_enc)
        header = headers.get('Stripe-Signature') or ''
        if not secret or not header:
            return False
        parts = [p.split('=', 1) for p in header.split(',') if '=' in p]
        stamp = next((v for k, v in parts if k.strip() == 't'), '')
        sigs = [v.strip() for k, v in parts if k.strip() == 'v1']
        if not stamp.isdigit() or abs(time.time() - int(stamp)) > TOLERANCE:
            return False
        expected = hmac.new(secret.encode(), stamp.encode() + b'.' + raw_body, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in sigs)

    @staticmethod
    def webhook_references(payload):
        obj = (payload.get('data') or {}).get('object') or {}
        refs = [obj.get('id'), obj.get('client_reference_id'), (obj.get('metadata') or {}).get('reference')]
        return [str(r) for r in refs if r]
