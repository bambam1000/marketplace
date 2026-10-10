"""Menu « Mon compte » partagé par le profil, les commandes, les devis…"""
from django.db.models import Q

ACTIVE_ORDER = ('pending', 'confirmed', 'processing', 'shipped')


def account_nav(user, active):
    from orders.models import Order, RFQ
    from catalog.models import Wishlist
    from messaging.models import Notification
    from messaging import services as msg
    orders = Order.objects.filter(buyer=user)
    return {
        'active': active,
        'orders_active': orders.filter(status__in=ACTIVE_ORDER).count(),
        'to_pay': orders.filter(status='pending', is_paid=False).exclude(payment_method='cash').count(),
        'rfqs_to_review': RFQ.objects.filter(buyer=user, status__in=('open', 'quoted'), quotes__status='pending').distinct().count(),
        'unread_messages': msg.unread_count(user),
        'unread_notifs': Notification.objects.filter(user=user, is_read=False).count(),
        'wishlist': Wishlist.objects.filter(user=user).count(),
    }


def profile_completion(user):
    """Champs utiles pour commander vite : [(libellé, rempli)]"""
    return [
        ('Prénom et nom', bool(user.first_name and user.last_name)),
        ('E-mail', bool(user.email)),
        ('Téléphone', bool(user.phone)),
        ('Adresse de livraison', bool(user.address)),
        ('Ville', bool(user.city)),
    ]


def search_q(q):
    """Filtre de recherche d'une commande : numéro ou nom de produit."""
    return Q(order_number__icontains=q) | Q(items__product__name__icontains=q)
