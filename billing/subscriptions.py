"""Cycle de vie des abonnements : demande, validation du paiement, rappels et expiration.

Un abonnement payant n'est jamais activé sur la foi du vendeur : il demande une formule (paiement « en attente »),
un administrateur vérifie la réception du paiement puis le valide, ce qui active la formule.
"""
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import Subscription, SubscriptionPayment

REMINDER_DAYS = (7, 2)
CYCLE_DAYS = {'monthly': 30, 'yearly': 365}


def amount_for(plan, cycle):
    return plan.price_yearly if cycle == 'yearly' and plan.price_yearly else plan.price_monthly


def request_plan(user, plan, cycle, method, phone=''):
    """Enregistre la demande d'abonnement (paiement en attente) et prévient les administrateurs."""
    from .limits import start_trial
    cycle = cycle if cycle in CYCLE_DAYS else 'monthly'
    sub = start_trial(user)
    payment = SubscriptionPayment.objects.filter(subscription=sub, status='pending').first()
    if payment:  # une seule demande en cours : on la remplace
        payment.plan, payment.billing_cycle, payment.amount = plan, cycle, amount_for(plan, cycle)
        payment.payment_method, payment.payer_phone = method, phone[:20]
        payment.save()
    else:
        payment = SubscriptionPayment.objects.create(subscription=sub, plan=plan, billing_cycle=cycle,
                                                     amount=amount_for(plan, cycle), payment_method=method,
                                                     payer_phone=phone[:20], status='pending')
    _notify_admins(payment)
    return payment


def _notify_admins(payment):
    from accounts.models import User
    from messaging.utils import notify
    user = payment.subscription.user
    for admin in User.objects.filter(is_active=True).filter(is_superuser=True) | User.objects.filter(is_active=True, role='admin'):
        notify(admin, 'account', f'Abonnement à valider : {user.display_name}',
               f'{payment.plan.name} ({payment.get_billing_cycle_display().lower()}), {int(payment.amount):,} F '
               f'par {payment.get_payment_method_display()}'.replace(',', ' ')
               + (f' depuis le {payment.payer_phone}' if payment.payer_phone else '') + '. Vérifiez la réception puis validez.',
               url=reverse('dashboard:admin_subscriptions'))


def validate(payment, admin):
    """Paiement reçu : active la formule. Un renouvellement de la même formule s'ajoute à la période en cours."""
    with transaction.atomic():
        payment = SubscriptionPayment.objects.select_for_update().select_related('subscription', 'plan').get(pk=payment.pk)
        if payment.status != 'pending':
            return payment
        sub = payment.subscription
        now = timezone.now()
        start = sub.end_date if (sub.is_active and sub.plan_id == payment.plan_id and sub.status == 'active') else now
        sub.plan, sub.status, sub.billing_cycle = payment.plan, 'active', payment.billing_cycle
        sub.end_date = max(start, now) + timedelta(days=CYCLE_DAYS[payment.billing_cycle])
        sub.boost_credits_remaining = payment.plan.boost_credits_monthly
        sub.reminders_sent = ''
        sub.granted_by, sub.grant_note = None, ''
        sub.save()
        payment.status, payment.validated_by, payment.validated_at = 'completed', admin, now
        payment.save()
        from accounting.models import Transaction
        Transaction.objects.create(user=sub.user, type='subscription', amount=payment.amount, status='completed',
                                   payment_method=payment.payment_method,
                                   reference=f'Abonnement {payment.plan.name} ({payment.get_billing_cycle_display().lower()})')
        _restore_stores(sub.user)
    from messaging.utils import notify
    notify(sub.user, 'account', f'Formule {sub.plan.name} activée',
           f'Votre paiement est validé : la formule {sub.plan.name} est active jusqu\'au '
           f'{timezone.localtime(sub.end_date):%d/%m/%Y}.', url=reverse('billing:my_subscription'), send_email=True)
    return payment


def reject(payment, admin, reason=''):
    payment.status, payment.validated_by, payment.validated_at = 'failed', admin, timezone.now()
    payment.save()
    from messaging.utils import notify
    notify(payment.subscription.user, 'account', 'Paiement d\'abonnement non reçu',
           'Nous n\'avons pas pu confirmer votre paiement' + (f' : {reason}' if reason else '.') +
           ' Contactez-nous ou refaites votre demande.', url=reverse('billing:plans'), send_email=True)


def _restore_stores(user):
    from store.models import Store
    Store.objects.filter(owner=user, billing_suspended=True).update(is_active=True, billing_suspended=False)


def _suspend_stores(user):
    from store.models import Store
    Store.objects.filter(owner=user, is_active=True).update(is_active=False, billing_suspended=True)


def run_daily(now=None, dry_run=False, report=None):
    """Rappels avant la fin (J-7, J-2) puis expiration : la boutique est masquée jusqu'au paiement.
    dry_run : ne modifie rien et n'envoie rien, `report` (liste) reçoit ce qui serait fait.
    Retourne (rappels envoyés, abonnements expirés)."""
    from messaging.utils import notify
    now = now or timezone.now()
    reminders = expired = 0
    for sub in Subscription.objects.filter(status__in=['trial', 'active']).select_related('plan', 'user'):
        if sub.end_date <= now and dry_run:
            if report is not None:
                report.append(f'Expirerait : {sub.user.display_name} ({sub.plan.name}, échu le {timezone.localtime(sub.end_date):%d/%m/%Y})'
                              ' : boutique masquée')
            expired += 1
            continue
        if sub.end_date <= now:
            sub.status = 'expired'
            sub.save(update_fields=['status'])
            _suspend_stores(sub.user)
            what = 'Votre essai gratuit est terminé' if sub.plan.is_trial else f'Votre formule {sub.plan.name} a expiré'
            notify(sub.user, 'account', what,
                   f'{what} : votre boutique n\'est plus visible et vous ne pouvez plus ajouter de produits. '
                   'Vos données sont conservées : choisissez une formule pour tout retrouver.',
                   url=reverse('billing:plans'), send_email=True)
            expired += 1
            continue
        days_left = (sub.end_date - now).days + 1
        sent = set(filter(None, sub.reminders_sent.split(',')))
        for day in REMINDER_DAYS:
            if days_left <= day and str(day) not in sent and dry_run:
                if report is not None:
                    report.append(f'Rappel J-{day} : {sub.user.display_name} ({sub.plan.name}, fin le {timezone.localtime(sub.end_date):%d/%m/%Y})')
                reminders += 1
                break
            if days_left <= day and str(day) not in sent:
                what = 'Votre essai gratuit' if sub.plan.is_trial else f'Votre formule {sub.plan.name}'
                notify(sub.user, 'account', f'{what} se termine dans {days_left} jour{"s" if days_left > 1 else ""}',
                       f'{what} se termine le {timezone.localtime(sub.end_date):%d/%m/%Y}. '
                       'Choisissez une formule pour continuer à vendre sans interruption.',
                       url=reverse('billing:plans'), send_email=True)
                sent.update(str(d) for d in REMINDER_DAYS if d >= day)
                sub.reminders_sent = ','.join(sorted(sent))
                sub.save(update_fields=['reminders_sent'])
                reminders += 1
                break
    return reminders, expired


UNLIMITED_END = timezone.make_aware(timezone.datetime(2099, 12, 31))


def grant(user, plan, days, admin, note=''):
    """Accès offert par un administrateur, sans paiement : `days` jours, ou sans limite si days est None."""
    from .limits import start_trial
    sub = start_trial(user)
    sub.plan, sub.status, sub.billing_cycle = plan, 'active', 'monthly'
    sub.end_date = UNLIMITED_END if days is None else timezone.now() + timedelta(days=days)
    sub.granted_by, sub.grant_note = admin, note[:200]
    sub.boost_credits_remaining = plan.boost_credits_monthly
    sub.reminders_sent = ''
    sub.save()
    SubscriptionPayment.objects.filter(subscription=sub, status='pending').update(status='failed')  # demande devenue inutile
    _restore_stores(user)
    from messaging.utils import notify
    until = 'sans limite de durée' if days is None else f"jusqu'au {timezone.localtime(sub.end_date):%d/%m/%Y}"
    notify(user, 'account', f'Accès {plan.name} offert',
           f'Comptoir vous offre la formule {plan.name}, {until}. Aucun paiement n\'est demandé.',
           url=reverse('billing:my_subscription'), send_email=True)
    return sub


def revoke(sub, admin):
    """Retire l'accès offert : l'abonnement expire et la boutique est masquée, comme à la fin d'une formule."""
    sub.status, sub.end_date = 'expired', timezone.now()
    sub.save(update_fields=['status', 'end_date'])
    _suspend_stores(sub.user)
    return sub
