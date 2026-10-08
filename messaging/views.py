from django.contrib import messages as flash
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from .models import Conversation, Message, Notification
from . import services


def _layout(user):
    """Vendeurs, administrateurs et employés restent dans le tableau de bord ; les acheteurs sur le site."""
    from store import access
    if access.is_admin(user) or user.is_seller or access.acting_store(user) is not None:
        return 'dashboard/base.html'
    return 'base.html'


def _conversation_or_404(user, pk):
    convo = get_object_or_404(Conversation.objects.select_related('buyer', 'seller', 'product'), pk=pk)
    if not services.can_access(user, convo):
        raise Http404
    return convo


def _wants_json(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('accept', '')


def _messenger(request, convo=None):
    query = request.GET.get('q', '').strip()[:80]
    only_unread = request.GET.get('filtre') == 'non-lus'
    conversations = list(services.inbox(request.user, query, only_unread))
    for c in conversations:
        c.other = services.counterpart(request.user, c)
        c.when = services.short_when(c.last_at)
        c.last_mine = c.last_sender == request.user.pk or (
            not services.is_buyer_side(request.user, c) and c.last_sender != c.buyer_id)
    context = {
        'layout': _layout(request.user), 'conversations': conversations, 'q': query, 'only_unread': only_unread,
        'conversation': convo, 'max_length': services.MAX_LENGTH,
        'total_unread': services.unread_count(request.user),
    }
    if convo is not None:
        msgs = list(convo.messages.select_related('sender'))
        context.update(
            other=services.counterpart(request.user, convo),
            messages_list=[services.message_payload(m, request.user, convo) | {'obj': m} for m in msgs],
            last_id=msgs[-1].pk if msgs else 0,
        )
    return render(request, 'messaging/messenger.html', context)


@login_required
def inbox(request):
    return _messenger(request)


@login_required
def conversation(request, pk):
    convo = _conversation_or_404(request.user, pk)
    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if not content:
            if _wants_json(request):
                return JsonResponse({'error': 'Le message est vide.'}, status=400)
            return redirect('messaging:conversation', pk=convo.pk)
        if len(content) > services.MAX_LENGTH:
            error = f'Message trop long ({len(content)} caractères, {services.MAX_LENGTH} au maximum).'
            if _wants_json(request):
                return JsonResponse({'error': error}, status=400)
            flash.error(request, error)
            return redirect('messaging:conversation', pk=convo.pk)
        msg = Message.objects.create(conversation=convo, sender=request.user, content=content)
        convo.save(update_fields=['updated_at'])
        if _wants_json(request):
            return JsonResponse({'message': services.message_payload(msg, request.user, convo)})
        return redirect('messaging:conversation', pk=convo.pk)  # pas de renvoi du formulaire au rechargement

    services.incoming(request.user, Conversation.objects.filter(pk=convo.pk)).filter(is_read=False).update(is_read=True)
    return _messenger(request, convo)


@login_required
def new_conversation(request, seller_id):
    """Contacter une boutique (depuis un produit ou une boutique) ou un client (depuis le tableau de bord)."""
    other = get_object_or_404(User, pk=seller_id, is_active=True)
    product = None
    raw = request.GET.get('product', '')
    if raw.isdigit():
        from catalog.models import Product
        product = Product.objects.filter(pk=raw).select_related('store').first()
    convo = services.start_conversation(request.user, other, product)
    if convo is None:
        flash.error(request, "Impossible d'ouvrir cette conversation.")
        return redirect('messaging:inbox')
    return redirect('messaging:conversation', pk=convo.pk)


@login_required
def get_unread_count(request):
    """Nombre de messages non lus (badge de l'en-tête)."""
    return JsonResponse({'unread_count': services.unread_count(request.user)})


@login_required
def get_new_messages(request, conversation_id):
    """Messages arrivés depuis `last_id`, et le dernier de mes messages lu par l'autre côté."""
    convo = Conversation.objects.filter(pk=conversation_id).select_related('buyer', 'seller').first()
    if convo is None or not services.can_access(request.user, convo):
        return JsonResponse({'error': 'Conversation introuvable'}, status=404)
    raw = request.GET.get('last_id', '0')
    last_id = int(raw) if raw.isdigit() else 0
    new = list(convo.messages.filter(pk__gt=last_id).select_related('sender'))
    incoming = services.incoming(request.user, Conversation.objects.filter(pk=convo.pk))
    incoming.filter(is_read=False).update(is_read=True)
    payload = [services.message_payload(m, request.user, convo) for m in new]
    mine_read = (convo.messages.filter(is_read=True).exclude(pk__in=incoming.values('pk'))
                 .order_by('-pk').values_list('pk', flat=True).first() or 0)
    return JsonResponse({'messages': payload, 'count': len(payload), 'read_upto': mine_read,
                         'unread_total': services.unread_count(request.user)})


# ========== NOTIFICATIONS ==========
@login_required
def notifications_list(request):
    """Centre de notifications"""
    notifs = Notification.objects.filter(user=request.user)
    # Marquer comme lues à l'affichage de la page
    notifs.filter(is_read=False).update(is_read=True)
    return render(request, 'messaging/notifications.html', {'notifications': notifs[:100]})


@login_required
def notification_mark_read(request, pk):
    """Marque une notification comme lue et redirige vers sa cible"""
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.is_read = True
    notif.save(update_fields=['is_read'])
    if notif.url:
        # Vérifier que l'objet lié existe encore (évite les 404)
        import re
        m = re.search(r'/commandes/([A-Z0-9-]+)/', notif.url)
        if m:
            from orders.models import Order
            if not Order.objects.filter(order_number=m.group(1)).exists():
                return redirect('messaging:notifications')
        return redirect(notif.url)
    return redirect('messaging:notifications')


@login_required
def notifications_mark_all_read(request):
    """Marque toutes les notifications comme lues"""
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect(request.META.get('HTTP_REFERER', 'messaging:notifications'))
