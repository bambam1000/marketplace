"""Paiement en ligne : chaque boutique encaisse sa part d'une commande sur son propre compte.

Règle de sécurité : une commande n'est jamais marquée payée sur la foi du navigateur du client.
Seule la réponse du prestataire (interrogé par le serveur) fait foi : montant, devise et statut sont contrôlés.
"""
import logging
from collections import OrderedDict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .gateways import GatewayError, client_for
from .models import OnlinePayment, PaymentGateway

log = logging.getLogger(__name__)
OPEN_ORDER = ('pending', 'confirmed', 'processing')


PROVIDER_ORDER = ['notchpay', 'stripe']
# Ce que le client voit sur le bouton de chaque prestataire
PROVIDER_UI = {
    'notchpay': {'label': 'Mobile Money ou carte', 'hint': 'MTN MoMo, Orange Money, carte', 'icon': 'fa-mobile-screen-button'},
    'stripe': {'label': 'Carte internationale', 'hint': 'Visa, Mastercard, Apple Pay, Google Pay', 'icon': 'fa-credit-card'},
}


def gateways_of(store, live_only=True):
    """Comptes de paiement utilisables d'une boutique (Notch Pay d'abord, puis Stripe)."""
    gws = [g for g in PaymentGateway.objects.filter(store=store, is_active=True).exclude(public_key_enc='')
           if g.live or not live_only]
    return sorted(gws, key=lambda g: PROVIDER_ORDER.index(g.provider) if g.provider in PROVIDER_ORDER else 99)


def gateway_of(store, live_only=True):
    gws = gateways_of(store, live_only)
    return gws[0] if gws else None


def store_shares(order):
    """{boutique: montant} dont la somme vaut exactement le total de la commande.
    Articles de chaque boutique, remise du code promo sur sa boutique, livraison et fidélité réparties au prorata."""
    items = OrderedDict()
    for it in order.items.select_related('store'):
        if it.store_id:
            items[it.store] = items.get(it.store, 0) + int(it.price * it.quantity)
    if not items:
        return {}
    shares = dict(items)
    promo = order.promo_code
    if promo and order.discount_amount and getattr(promo, 'store_id', None):
        for st in shares:
            if st.pk == promo.store_id:
                shares[st] -= int(order.discount_amount)
    diff = int(order.total_amount) - sum(shares.values())
    base = sum(items.values()) or 1
    spread = {st: diff * items[st] // base for st in shares}
    rest = diff - sum(spread.values())
    biggest = max(items, key=items.get)
    spread[biggest] += rest
    return OrderedDict((st, max(0, shares[st] + spread[st])) for st in items)


def order_parts(order):
    """[{store, amount, payment (dernier paiement), paid, gateway}] pour la page « Payer »."""
    parts = []
    for st, amount in store_shares(order).items():
        pays = list(order.online_payments.filter(store=st))
        paid = next((p for p in pays if p.status == 'paid'), None)
        gws = gateways_of(st)
        parts.append({'store': st, 'amount': amount, 'paid': paid, 'last': pays[0] if pays else None,
                      'gateway': gws[0] if gws else None,
                      'options': [{'provider': g.provider, **PROVIDER_UI.get(g.provider, {})} for g in gws]})
    return parts


def start(order, store, user, callback_url, provider=None, cancel_url=''):
    """Crée le paiement de la part d'une boutique et renvoie l'adresse de la page de paiement."""
    if order.status not in OPEN_ORDER or order.payment_method != 'online':
        raise GatewayError('Cette commande ne peut plus être payée en ligne.')
    shares = store_shares(order)
    amount = next((a for st, a in shares.items() if st.pk == store.pk), None)
    if amount is None:
        raise GatewayError('Cette boutique ne fait pas partie de la commande.')
    if order.online_payments.filter(store=store, status='paid').exists():
        raise GatewayError('Cette partie de la commande est déjà payée.')
    if amount <= 0:
        raise GatewayError('Rien à payer pour cette boutique.')
    gws = gateways_of(store)
    gw = next((g for g in gws if g.provider == provider), None) if provider else (gws[0] if gws else None)
    if not gw:
        raise GatewayError(f"{store.name} n'accepte plus ce moyen de paiement en ligne. Choisissez-en un autre ou contactez la boutique.")
    pay = OnlinePayment.objects.create(gateway=gw, order=order, store=store, payer=user, amount=amount, currency='XAF')
    return _create(pay, {'name': order.shipping_name or user.get_full_name(), 'email': user.email, 'phone': order.shipping_phone},
                   callback_url, f'Commande {order.order_number} · {store.name}', cancel_url)


def start_test(gateway, user, callback_url):
    """Paiement d'essai du vendeur (mode test conseillé) : n'est rattaché à aucune commande."""
    amount = client_for(gateway).test_amount
    pay = OnlinePayment.objects.create(gateway=gateway, store=gateway.store, payer=user, amount=amount, currency='XAF',
                                       is_test=True)
    return _create(pay, {'name': user.get_full_name() or user.username, 'email': user.email, 'phone': user.phone or ''},
                   callback_url, f'Paiement d\'essai Comptoir · {gateway.store.name}')


def _create(pay, customer, callback_url, description, cancel_url=''):
    try:
        url, ref = client_for(pay.gateway).create(pay, customer, callback_url, description, cancel_url)
    except GatewayError as exc:
        pay.status, pay.last_error = 'failed', str(exc)[:300]
        pay.save(update_fields=['status', 'last_error', 'updated_at'])
        raise
    pay.checkout_url, pay.provider_ref = url[:500], ref
    pay.save(update_fields=['checkout_url', 'provider_ref', 'updated_at'])
    return pay


def refresh(pay):
    """Demande l'état au prestataire et l'applique. Retourne le paiement à jour."""
    if pay.status == 'paid':
        return pay
    result = client_for(pay.gateway).retrieve(pay)
    apply_result(pay.pk, result)
    pay.refresh_from_db()
    return pay


def apply_result(pay_id, result):
    """Applique l'état renvoyé par le prestataire (idempotent : un paiement n'est compté qu'une fois)."""
    with transaction.atomic():
        pay = OnlinePayment.objects.select_for_update().select_related('order', 'store').get(pk=pay_id)
        if pay.status == 'paid':
            return pay
        pay.raw = result.raw if isinstance(result.raw, dict) else {}
        if result.channel:
            pay.channel = result.channel
        if result.status == 'paid':
            if result.amount is None or result.amount < pay.amount or (result.currency and result.currency != pay.currency):
                pay.status = 'failed'
                pay.last_error = (f'Montant reçu incohérent ({result.amount} {result.currency} au lieu de '
                                  f'{pay.amount} {pay.currency}) : vérifiez dans votre compte de paiement.')[:300]
                pay.save()
                log.warning('Paiement %s : montant incohérent %s', pay.reference, result.amount)
                return pay
            pay.status, pay.paid_at, pay.last_error = 'paid', timezone.now(), ''
            pay.save()
            if pay.order_id:
                transaction.on_commit(lambda: _on_paid(pay.pk))
        elif result.status in ('failed', 'cancelled', 'expired'):
            pay.status = result.status
            pay.save()
        else:
            pay.save(update_fields=['raw', 'channel', 'updated_at'])
        return pay


def _on_paid(pay_id):
    """Une part payée : la boutique est prévenue ; toute la commande payée → commande confirmée."""
    from messaging.utils import notify, notify_store
    from orders.models import Order, OrderStatusEvent
    pay = OnlinePayment.objects.select_related('order', 'store').get(pk=pay_id)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=pay.order_id)
        paid_stores = set(order.online_payments.filter(status='paid').values_list('store_id', flat=True))
        all_paid = all(st.pk in paid_stores for st in store_shares(order))
        newly = all_paid and not order.is_paid
        if newly:
            order.is_paid = True
            fields = ['is_paid', 'updated_at']
            if order.status == 'pending':
                order.status = 'confirmed'
                fields.append('status')
                OrderStatusEvent.objects.create(order=order, status='confirmed', by=None, note='Payée en ligne')
            order.save(update_fields=fields)
    amount = f'{int(pay.amount):,}'.replace(',', ' ')
    notify_store(pay.store, 'orders.view', 'order', f'Paiement reçu : {order.order_number}',
                 f'{amount} FCFA payés en ligne{f" ({pay.channel})" if pay.channel else ""}. '
                 + ('La commande est confirmée : préparez-la.' if all_paid else 'Votre part de la commande est réglée.'),
                 url=f'/dashboard/commandes/{order.order_number}/', send_email=True)
    if newly:
        notify(order.buyer, 'order', f'Paiement reçu pour la commande {order.order_number}',
               'Merci ! Votre paiement est confirmé, la boutique prépare votre commande.',
               url=f'/commandes/{order.order_number}/', send_email=True)


def check_pending(max_age_hours=48, limit=200):
    """Revérifie les paiements restés en attente (notification perdue, client qui n'est pas revenu)."""
    from datetime import timedelta
    since = timezone.now() - timedelta(hours=max_age_hours)
    done = errors = 0
    for pay in OnlinePayment.objects.filter(status='pending', created_at__gte=since).exclude(provider_ref='', checkout_url='')[:limit]:
        try:
            refresh(pay)
            done += 1
        except GatewayError:
            errors += 1
    expired = OnlinePayment.objects.filter(status='pending', created_at__lt=since).update(status='expired')
    return done, errors, expired


def amount_label(value):
    return f'{int(Decimal(value)):,}'.replace(',', ' ')
