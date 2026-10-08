

def unread_messages_count(request):
    """Context processor : messages non lus (côté acheteur ou côté boutique, employés compris)."""
    if request.user.is_authenticated:
        from .services import unread_count
        return {'unread_messages_count': unread_count(request.user)}
    return {'unread_messages_count': 0}


def notifications_context(request):
    """Context processor : notifications non lues + dernières notifications"""
    if request.user.is_authenticated:
        from .models import Notification
        qs = Notification.objects.filter(user=request.user)
        return {
            'unread_notifications_count': qs.filter(is_read=False).count(),
            'latest_notifications': qs[:8],
        }
    return {'unread_notifications_count': 0, 'latest_notifications': []}
