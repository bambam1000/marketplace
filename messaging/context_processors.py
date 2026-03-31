from .models import Message, Conversation
from django.db.models import Q


def unread_messages_count(request):
    """Context processor pour afficher le nombre de messages non lus"""
    if request.user.is_authenticated:
        # Récupérer toutes les conversations de l'utilisateur
        user_conversations = Conversation.objects.filter(
            Q(buyer=request.user) | Q(seller=request.user)
        ).values_list('id', flat=True)

        # Compter les messages non lus dans ces conversations
        unread_count = Message.objects.filter(
            conversation_id__in=user_conversations,
            is_read=False
        ).exclude(
            sender=request.user
        ).count()

        return {'unread_messages_count': unread_count}

    return {'unread_messages_count': 0}
