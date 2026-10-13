

def unread_messages_count(request):
    """Context processor : messages non lus (côté acheteur ou côté boutique, employés compris)."""
    if request.user.is_authenticated:
        from .services import unread_count
        return {'unread_messages_count': unread_count(request.user)}
    return {'unread_messages_count': 0}


def notifications_context(request):
    """Context processor : nombre de non lues + les non lues récentes (menu de la cloche)"""
    if request.user.is_authenticated:
        from .models import Notification
        unread = Notification.objects.filter(user=request.user, is_read=False)
        return {
            'unread_notifications_count': unread.count(),
            # Une notification lue disparaît du menu de la cloche (elle reste dans la page Notifications)
            'latest_notifications': unread[:8],
        }
    return {'unread_notifications_count': 0, 'latest_notifications': []}


def site_alerts(request):
    """Alertes du haut de page (messaging/alerts.py), calculées seulement si le gabarit les affiche."""
    from .alerts import alerts_for
    return {'site_alerts': lambda: alerts_for(request),
            'dash_alerts': lambda: alerts_for(request, in_dash=True)}
