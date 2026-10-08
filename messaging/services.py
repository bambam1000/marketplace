"""Règles de la messagerie acheteurs ↔ boutiques.

Une conversation a deux côtés :
- le côté acheteur : `conversation.buyer` ;
- le côté boutique : `conversation.seller` (propriétaire de la boutique) et ses employés actifs
  qui ont le droit « Voir les clients ».
Un message est « reçu » par un côté s'il a été écrit par l'autre côté.
"""
from django.db.models import Count, F, OuterRef, Q, Subquery

from .models import Conversation, Message

MAX_LENGTH = 2000
STAFF_PERMISSION = 'customers.view'


def staff_store_owner_ids(user):
    """Propriétaires des boutiques dont l'utilisateur est employé avec accès aux clients."""
    from store.models import StoreMember
    members = StoreMember.objects.filter(user=user, is_active=True).select_related('role', 'store')
    return [m.store.owner_id for m in members if m.has_permission(STAFF_PERMISSION)]


def conversations_for(user):
    owners = staff_store_owner_ids(user)
    return Conversation.objects.filter(Q(buyer=user) | Q(seller=user) | Q(seller_id__in=owners))


def can_access(user, conversation):
    if user.pk in (conversation.buyer_id, conversation.seller_id):
        return True
    return conversation.seller_id in staff_store_owner_ids(user)


def is_buyer_side(user, conversation):
    return user.pk == conversation.buyer_id and user.pk != conversation.seller_id


def incoming(user, conversations=None):
    """Messages reçus par l'utilisateur (écrits par l'autre côté), dans ses conversations."""
    conversations = conversations if conversations is not None else conversations_for(user)
    return Message.objects.filter(conversation__in=conversations).filter(
        # côté acheteur : tout ce qui ne vient pas de l'acheteur ; côté boutique : ce qui vient de l'acheteur
        (Q(conversation__buyer=user) & ~Q(sender=user)) |
        (~Q(conversation__buyer=user) & Q(sender=F('conversation__buyer')))
    )


def unread_count(user):
    return incoming(user).filter(is_read=False).count()


def inbox(user, query='', only_unread=False):
    """Conversations de l'utilisateur avec dernier message et nombre de non lus (une seule requête)."""
    last = Message.objects.filter(conversation=OuterRef('pk')).order_by('-created_at')
    unread_filter = Q(messages__is_read=False) & (
        (Q(buyer=user) & ~Q(messages__sender=user)) | (~Q(buyer=user) & Q(messages__sender=F('buyer'))))
    qs = (conversations_for(user)
          .annotate(n_messages=Count('messages', distinct=True),
                    unread=Count('messages', filter=unread_filter, distinct=True),
                    last_content=Subquery(last.values('content')[:1]),
                    last_at=Subquery(last.values('created_at')[:1]),
                    last_sender=Subquery(last.values('sender_id')[:1]))
          .filter(n_messages__gt=0)
          .select_related('buyer', 'seller', 'product')
          .order_by('-last_at'))
    if query:
        qs = qs.filter(Q(buyer__first_name__icontains=query) | Q(buyer__last_name__icontains=query) |
                       Q(buyer__username__icontains=query) | Q(seller__first_name__icontains=query) |
                       Q(seller__last_name__icontains=query) | Q(seller__username__icontains=query) |
                       Q(seller__stores__name__icontains=query) | Q(product__name__icontains=query) |
                       Q(messages__content__icontains=query)).distinct()
    if only_unread:
        qs = qs.filter(unread__gt=0)
    return qs


def counterpart(user, conversation):
    """Ce que l'utilisateur voit de l'autre côté : nom, initiales, rôle."""
    if is_buyer_side(user, conversation):
        seller = conversation.seller
        store = seller.store
        name = store.name if store else seller.display_name
        return {'name': name, 'initials': (name[:1] or '?').upper(), 'role': 'Boutique', 'store': store, 'user': seller}
    buyer = conversation.buyer
    return {'name': buyer.display_name, 'initials': buyer.initials, 'role': 'Client', 'store': None, 'user': buyer}


DAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']


def day_label(dt):
    from django.utils import timezone
    d = timezone.localtime(dt).date()
    diff = (timezone.localdate() - d).days
    if diff == 0:
        return "aujourd'hui"
    if diff == 1:
        return 'hier'
    return f'{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}'


def short_when(dt):
    """Heure si aujourd'hui, « Hier », sinon la date courte (liste des conversations)."""
    from django.utils import timezone
    if dt is None:
        return ''
    local = timezone.localtime(dt)
    diff = (timezone.localdate() - local.date()).days
    if diff == 0:
        return local.strftime('%H:%M')
    if diff == 1:
        return 'Hier'
    if diff < 7:
        return DAYS[local.weekday()][:3] + '.'
    return local.strftime('%d/%m/%y')


def message_payload(message, user, conversation):
    mine = (message.sender_id == user.pk) or (not is_buyer_side(user, conversation) and message.sender_id != conversation.buyer_id)
    return {
        'id': message.pk,
        'content': message.content,
        'mine': mine,
        'is_mine': mine,
        'sender_name': message.sender.display_name,
        'sender_id': message.sender_id,
        'time': message.created_at.isoformat(),
        'created_at': message.created_at.strftime('%H:%M'),
        'read': message.is_read,
        'day': day_label(message.created_at),
    }


def start_conversation(user, other, product=None):
    """Ouvre (ou retrouve) la conversation entre l'utilisateur et `other`.

    - acheteur → boutique : `other` est le vendeur ;
    - boutique → client : l'utilisateur est le vendeur (ou un employé autorisé), `other` est l'acheteur.
    Retourne None si la conversation n'a pas de sens (soi-même, deux acheteurs…).
    """
    from store import access
    if other.pk == user.pk:
        return None
    other_is_seller = other.is_seller or other.stores.exists()
    store = access.acting_store(user)
    if other_is_seller and not (store and store.owner_id == other.pk):
        buyer, seller = user, other
    elif store is not None and (user.pk == store.owner_id or store.owner_id in staff_store_owner_ids(user)):
        buyer, seller = other, store.owner
    else:
        return None
    convo, _ = Conversation.objects.get_or_create(buyer=buyer, seller=seller)
    if product is not None and product.store.owner_id == seller.pk and convo.product_id != product.pk:
        convo.product = product
        convo.save(update_fields=['product', 'updated_at'])
    return convo
