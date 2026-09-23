from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from .models import Conversation, Message, Notification
from accounts.models import User

@login_required
def inbox(request):
    convos = Conversation.objects.filter(
        buyer=request.user
    ) | Conversation.objects.filter(seller=request.user)
    convos = convos.distinct().order_by('-updated_at')

    # Ajouter le compteur de messages non lus pour chaque conversation
    for convo in convos:
        convo.unread_count = Message.objects.filter(
            conversation=convo,
            is_read=False
        ).exclude(sender=request.user).count()

    return render(request, 'messaging/inbox.html', {'conversations': convos})

@login_required
def conversation(request, pk):
    convo = get_object_or_404(Conversation, pk=pk)
    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if content:
            Message.objects.create(conversation=convo, sender=request.user, content=content)
            convo.save()  # updates updated_at
    msgs = convo.messages.all()
    msgs.filter(is_read=False).exclude(sender=request.user).update(is_read=True)
    all_convos = Conversation.objects.filter(
        buyer=request.user
    ) | Conversation.objects.filter(seller=request.user)
    return render(request, 'messaging/conversation.html', {
        'conversation': convo, 'messages_list': msgs,
        'conversations': all_convos.distinct().order_by('-updated_at'),
    })

@login_required
def new_conversation(request, seller_id):
    seller = get_object_or_404(User, pk=seller_id, role='seller')
    convo, _ = Conversation.objects.get_or_create(buyer=request.user, seller=seller)
    product_id = request.GET.get('product')
    if product_id:
        convo.product_id = product_id
        convo.save()
    return redirect('messaging:conversation', pk=convo.pk)


@login_required
def get_unread_count(request):
    """API pour récupérer le nombre de messages non lus"""
    user_conversations = Conversation.objects.filter(
        Q(buyer=request.user) | Q(seller=request.user)
    ).values_list('id', flat=True)

    unread_count = Message.objects.filter(
        conversation_id__in=user_conversations,
        is_read=False
    ).exclude(
        sender=request.user
    ).count()

    return JsonResponse({'unread_count': unread_count})


@login_required
def get_new_messages(request, conversation_id):
    """API pour récupérer les nouveaux messages d'une conversation"""
    conversation = get_object_or_404(Conversation, pk=conversation_id)

    # Vérifier que l'utilisateur fait partie de la conversation
    if conversation.buyer != request.user and conversation.seller != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    last_message_id = request.GET.get('last_id', 0)

    # Récupérer les nouveaux messages
    new_messages = Message.objects.filter(
        conversation=conversation,
        id__gt=last_message_id
    ).order_by('created_at')

    messages_data = [{
        'id': msg.id,
        'sender_name': msg.sender.display_name,
        'sender_id': msg.sender.id,
        'content': msg.content,
        'created_at': msg.created_at.strftime('%H:%M'),
        'is_mine': msg.sender == request.user
    } for msg in new_messages]

    # Marquer les messages comme lus
    new_messages.exclude(sender=request.user).update(is_read=True)

    return JsonResponse({
        'messages': messages_data,
        'count': len(messages_data)
    })


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
        return redirect(notif.url)
    return redirect('messaging:notifications')


@login_required
def notifications_mark_all_read(request):
    """Marque toutes les notifications comme lues"""
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect(request.META.get('HTTP_REFERER', 'messaging:notifications'))
