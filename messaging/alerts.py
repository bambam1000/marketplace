"""Alertes affichées en haut de chaque page : la communication de Comptoir avec ses utilisateurs.

Deux sources :
- automatiques, selon la situation de la personne (abonnement du vendeur, commandes à payer de l'acheteur,
  paiements à valider pour l'administrateur). Elles restent affichées tant que la situation dure ;
- annonces rédigées par l'administration (Admin > Annonces), ciblées par public et par dates.
"""
from datetime import timedelta

from django.db import DatabaseError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

MAX_ANNOUNCEMENTS = 2


def _alert(key, tone, icon, title, text='', link='', link_label='', dismissible=False):
    return {'key': key, 'tone': tone, 'icon': icon, 'title': title, 'text': text,
            'link': link, 'link_label': link_label, 'dismissible': dismissible}


def _plural(n, word):
    return f'{n} {word}{"s" if n > 1 else ""}'


def _seller_alerts(user, store, in_dash):
    from billing.limits import subscription_of
    from billing.models import SubscriptionPayment
    sub = subscription_of(store)
    if sub is None:
        return []
    out = []
    plans, mine = reverse('billing:plans'), reverse('billing:my_subscription')
    pending = SubscriptionPayment.objects.filter(subscription=sub, status='pending').select_related('plan').first()
    if pending:
        out.append(_alert('sub-pending', 'info', 'fa-hourglass-half', f'Formule {pending.plan.name} : paiement en attente',
                          f'Votre demande de {int(pending.amount):,} F est enregistrée. La formule est activée dès réception '
                          f'de votre paiement.'.replace(',', ' '), mine, 'Voir ma demande'))
    recent = SubscriptionPayment.objects.filter(subscription=sub, status='completed',
                                                validated_at__gte=timezone.now() - timedelta(days=3)).select_related('plan').first()
    if recent:
        out.append(_alert(f'sub-ok-{recent.pk}', 'success', 'fa-circle-check', f'Paiement reçu : formule {recent.plan.name} activée',
                          f"Votre abonnement court jusqu'au {timezone.localtime(sub.end_date):%d/%m/%Y}.", mine,
                          'Mon abonnement', dismissible=True))
    if in_dash:
        return out  # essai, échéance et expiration : bandeau propre au dashboard
    if not sub.is_active:
        what = 'Votre essai gratuit est terminé' if sub.plan.is_trial else f'Votre formule {sub.plan.name} a expiré'
        out.insert(0, _alert('sub-expired', 'danger', 'fa-circle-exclamation', what,
                             "Votre boutique n'est plus visible des acheteurs. Vos données sont conservées.",
                             plans, 'Choisir une formule'))
    elif pending:
        pass  # la demande en cours suffit
    elif sub.plan.is_trial:
        days = sub.days_remaining
        out.insert(0, _alert('sub-trial', 'warning' if days <= 7 else 'promo', 'fa-gift',
                             f'Essai gratuit : encore {_plural(days, "jour")}',
                             'Choisissez votre formule pour continuer à vendre sans interruption.', plans, 'Voir les formules'))
    elif not sub.granted_by_id and sub.days_remaining <= 7:
        days = sub.days_remaining
        out.insert(0, _alert('sub-ending', 'warning', 'fa-clock', f'Votre formule {sub.plan.name} se termine dans {_plural(days, "jour")}',
                             f'Renouvelez-la avant le {timezone.localtime(sub.end_date):%d/%m/%Y} pour que votre boutique reste visible.',
                             plans, 'Renouveler'))
    return out


def _buyer_alerts(user):
    from orders.models import Order
    to_pay = list(Order.objects.filter(buyer=user, status='pending', is_paid=False).exclude(payment_method='cash')
                  .exclude(shipping_address=Order.DIRECT_SALE_ADDRESS).order_by('-created_at')[:2])
    if not to_pay:
        return []
    count = Order.objects.filter(buyer=user, status='pending', is_paid=False).exclude(payment_method='cash') \
        .exclude(shipping_address=Order.DIRECT_SALE_ADDRESS).count()
    if count == 1:
        order = to_pay[0]
        return [_alert('orders-to-pay', 'warning', 'fa-credit-card', f'Commande {order.order_number} en attente de paiement',
                       f'{int(order.total_amount):,} F à régler pour que la boutique prépare votre commande.'.replace(',', ' '),
                       reverse('orders:detail', args=[order.order_number]), 'Payer ma commande')]
    return [_alert('orders-to-pay', 'warning', 'fa-credit-card', f'{count} commandes en attente de paiement',
                   'Réglez-les pour que les boutiques les préparent.', reverse('orders:list') + '?status=to_pay', 'Voir mes commandes')]


def _admin_alerts():
    from billing.models import SubscriptionPayment
    n = SubscriptionPayment.objects.filter(status='pending').count()
    if not n:
        return []
    return [_alert('admin-sub-payments', 'info', 'fa-gem', f'{_plural(n, "paiement")} d\'abonnement à valider',
                   'Vérifiez la réception des paiements pour activer les formules des vendeurs.',
                   reverse('dashboard:admin_subscriptions'), 'Valider')]


def _announcements(user, is_seller, in_dash, is_admin=False):
    from .models import Announcement
    now = timezone.now()
    if is_admin:
        audiences = ['all']  # l'administration n'est pas la cible des invitations vendeurs / acheteurs
    elif in_dash:
        audiences = ['all', 'sellers']
    elif not user.is_authenticated:
        audiences = ['all', 'visitors', 'buyers']
    elif is_seller:
        audiences = ['all', 'sellers']
    else:
        audiences = ['all', 'buyers']
    qs = (Announcement.objects.filter(is_active=True, audience__in=audiences)
          .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)))
    icons = {'info': 'fa-bullhorn', 'promo': 'fa-store', 'success': 'fa-circle-check', 'warning': 'fa-triangle-exclamation'}
    return [_alert(f'ann-{a.pk}-{int(a.updated_at.timestamp())}', a.tone, icons.get(a.tone, 'fa-bullhorn'), a.title, a.message,
                   a.link_url, a.link_label or ('En savoir plus' if a.link_url else ''), dismissible=a.dismissible)
            for a in qs[:MAX_ANNOUNCEMENTS]]


def alerts_for(request, in_dash=False):
    """Alertes de la personne connectée (ou du visiteur), les plus importantes d'abord."""
    user = request.user
    out = []
    is_seller = admin = False
    if user.is_authenticated:
        from store.access import acting_store, is_admin
        admin = is_admin(user)
        if admin:
            out += _admin_alerts()
        store = acting_store(user)
        is_seller = store is not None
        if store is not None and store.owner_id == user.pk:
            out += _seller_alerts(user, store, in_dash)
        if not in_dash:
            out += _buyer_alerts(user)
    try:
        out += _announcements(user, is_seller, in_dash, is_admin=admin)
    except DatabaseError:  # table absente (migration pas encore appliquée) : le site s'affiche quand même
        pass
    return out
