"""Règles des demandes de devis (RFQ) et des offres, communes au site et au tableau de bord."""
from django.db import transaction

from .models import RFQ, Quote

ACCEPTING = ('open', 'quoted')   # une demande reçoit des offres tant qu'elle est ouverte ou a déjà des offres


class QuoteError(Exception):
    pass


def submit_quote(rfq, user, data):
    """Crée ou met à jour l'offre de la boutique de `user`. Retourne (offre, créée)."""
    store = user.store
    if store is None:
        raise QuoteError('Vous devez avoir une boutique pour proposer une offre.')
    if rfq.buyer_id == user.pk:
        raise QuoteError('Vous ne pouvez pas répondre à votre propre demande.')
    if rfq.status not in ACCEPTING:
        raise QuoteError("Cette demande n'accepte plus d'offres.")
    existing = Quote.objects.filter(rfq=rfq, seller=user).first()
    if existing and existing.status != 'pending':
        raise QuoteError("Votre offre a déjà été traitée par l'acheteur : elle ne peut plus être modifiée.")
    raw = str(data.get('price_per_unit', '')).strip().replace(' ', '')
    if not raw.isdigit() or not 0 < int(raw) <= 1_000_000_000:
        raise QuoteError('Indiquez un prix unitaire valide en FCFA.')
    price = int(raw)
    delivery = (data.get('delivery_time') or '').strip()[:100]
    if not delivery:
        raise QuoteError('Indiquez le délai de livraison (ex. : 7 à 10 jours).')
    terms = (data.get('payment_terms') or '').strip()[:200] or '50 % à la commande, 50 % à la livraison'
    description = (data.get('description') or '').strip()[:3000]
    with transaction.atomic():
        quote, created = Quote.objects.update_or_create(
            rfq=rfq, seller=user,
            defaults={'store': store, 'price_per_unit': price, 'total_price': price * rfq.quantity,
                      'delivery_time': delivery, 'payment_terms': terms, 'description': description})
        if rfq.status == 'open':
            rfq.status = 'quoted'
            rfq.save(update_fields=['status'])
    from messaging.utils import notify
    amount = f'{price * rfq.quantity:,}'.replace(',', ' ')
    unit_price = f'{price:,}'.replace(',', ' ')
    notify(rfq.buyer, 'rfq',
           f'{"Offre reçue" if created else "Offre mise à jour"} pour « {rfq.product_name} »',
           f'{store.name} vous propose {amount} FCFA ({unit_price} FCFA / {rfq.unit}), livraison : {delivery}.',
           url=f'/commandes/rfq/{rfq.pk}/', send_email=created)
    return quote, created


def accept_quote(quote, user):
    """L'acheteur accepte une offre : les autres offres en attente sont refusées, toutes les boutiques prévenues."""
    rfq = quote.rfq
    if rfq.buyer_id != user.pk:
        raise QuoteError("Vous n'êtes pas autorisé à accepter cette offre.")
    if rfq.status not in ACCEPTING or quote.status != 'pending':
        raise QuoteError('Cette offre ne peut plus être acceptée.')
    from messaging.utils import notify_store
    with transaction.atomic():
        quote.status = 'accepted'
        quote.save(update_fields=['status'])
        rfq.status = 'accepted'
        rfq.save(update_fields=['status'])
        others = list(Quote.objects.filter(rfq=rfq, status='pending').exclude(pk=quote.pk).select_related('store'))
        Quote.objects.filter(pk__in=[o.pk for o in others]).update(status='rejected')
    total = f'{int(quote.total_price):,}'.replace(',', ' ')
    notify_store(quote.store, 'orders.view', 'rfq', 'Votre offre a été acceptée',
                 f'{rfq.buyer.display_name} a accepté votre offre de {total} FCFA pour « {rfq.product_name} ». Contactez-le pour finaliser.',
                 url=f'/dashboard/devis/{rfq.pk}/', send_email=True)
    for other in others:
        notify_store(other.store, 'orders.view', 'rfq', 'Offre non retenue',
                     f'{rfq.buyer.display_name} a choisi une autre offre pour « {rfq.product_name} ». Merci pour votre réponse.',
                     url=f'/dashboard/devis/{rfq.pk}/')
    return quote


def reject_quote(quote, user):
    rfq = quote.rfq
    if rfq.buyer_id != user.pk:
        raise QuoteError("Vous n'êtes pas autorisé à refuser cette offre.")
    if quote.status != 'pending' or rfq.status not in ACCEPTING:
        raise QuoteError('Cette offre ne peut plus être refusée.')
    quote.status = 'rejected'
    quote.save(update_fields=['status'])
    from messaging.utils import notify_store
    notify_store(quote.store, 'orders.view', 'rfq', 'Offre non retenue',
                 f'{rfq.buyer.display_name} n\'a pas retenu votre offre pour « {rfq.product_name} ».',
                 url=f'/dashboard/devis/{rfq.pk}/')
    return quote


def close_rfq(rfq, user):
    if rfq.buyer_id != user.pk:
        raise QuoteError('Vous ne pouvez fermer que vos propres demandes.')
    if rfq.status not in ACCEPTING:
        raise QuoteError('Cette demande ne peut plus être fermée.')
    rfq.status = 'closed'
    rfq.save(update_fields=['status'])
    Quote.objects.filter(rfq=rfq, status='pending').update(status='rejected')
    return rfq
