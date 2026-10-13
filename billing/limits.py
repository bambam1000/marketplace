"""Ce que la formule d'une boutique autorise : limites (produits, entrepôts, caisses…) et fonctions incluses.

Toutes les vues passent par ici : `blocked(...)` renvoie le message à afficher, ou None si l'action est permise.
Un administrateur de la plateforme n'est jamais limité. L'abonnement est celui du propriétaire de la boutique ;
un vendeur qui n'en a pas encore reçoit l'essai gratuit.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import Subscription, SubscriptionPlan

RESOURCES = {
    # ressource : (champ de la formule, libellé au pluriel, libellé au singulier)
    'products': ('max_products', 'produits', 'produit'),
    'warehouses': ('max_warehouses', 'entrepôts', 'entrepôt'),
    'registers': ('max_registers', 'caisses', 'caisse'),
    'staff': ('max_staff', 'employés', 'employé'),
    'suppliers': ('max_suppliers', 'fournisseurs en dropshipping', 'fournisseur en dropshipping'),
    'featured': ('featured_products', 'produits mis en avant', 'produit mis en avant'),
}
FEATURES = {
    'marketing': ('can_marketing', 'Les campagnes marketing ne font'),
    'b2b': ('can_b2b', 'Les réponses aux demandes de devis ne font'),
    'payroll': ('can_payroll', 'La paie ne fait'),
    'whatsapp_inbox': ('can_whatsapp_inbox', 'Les conversations WhatsApp ne font'),
    'flash_deal': ('can_flash_deal', 'Les ventes flash ne font'),
    'boost': ('can_boost', 'Les boosts produits ne font'),
    'analytics': ('can_analytics', 'Les statistiques avancées ne font'),
    'dropship_auto': ('can_dropship_auto', 'La transmission automatique au fournisseur ne fait'),
}


def _is_admin(user):
    return bool(user and (user.is_superuser or getattr(user, 'role', '') == 'admin'))


def trial_plan():
    return SubscriptionPlan.objects.filter(is_trial=True, is_active=True).order_by('order').first()


def start_trial(user):
    """Donne l'essai gratuit à un vendeur qui n'a encore jamais eu d'abonnement. Retourne l'abonnement."""
    sub = Subscription.objects.filter(user=user).select_related('plan').first()
    if sub:
        return sub
    plan = trial_plan()
    if plan is None:
        return None
    return Subscription.objects.create(user=user, plan=plan, status='trial', auto_renew=False,
                                       end_date=timezone.now() + timedelta(days=plan.trial_days))


def subscription_of(store):
    if store is None:
        return None
    return start_trial(store.owner)


def is_live(store):
    sub = subscription_of(store)
    return bool(sub and sub.is_active)


def plan_of(store):
    """Formule en vigueur, ou None si l'abonnement a expiré."""
    sub = subscription_of(store)
    return sub.plan if sub and sub.is_active else None


def _upgrade_for(attr, need):
    """Formule payante la moins chère qui satisfait le besoin."""
    for plan in SubscriptionPlan.objects.filter(is_active=True, is_trial=False).order_by('price_monthly'):
        value = getattr(plan, attr)
        if isinstance(value, bool):
            if value:
                return plan
        elif value is None or value >= need:
            return plan
    return None


def _expired_message(store):
    sub = subscription_of(store)
    if sub and sub.plan.is_trial:
        return 'Votre essai gratuit est terminé : choisissez une formule pour continuer.'
    return 'Votre abonnement a expiré : renouvelez-le pour continuer.'


def count(store, resource):
    from inventory.models import Warehouse
    from pos.models import CashRegister
    if resource == 'products':
        return store.products.filter(is_active=True).count()
    if resource == 'warehouses':
        return Warehouse.objects.filter(store=store, is_active=True).exclude(kind='dropship').count()
    if resource == 'suppliers':
        return Warehouse.objects.filter(store=store, is_active=True, kind='dropship').count()
    if resource == 'registers':
        return CashRegister.objects.filter(store=store, is_active=True).count()
    if resource == 'staff':
        return store.members.filter(is_active=True).count()
    if resource == 'featured':
        return store.products.filter(is_active=True, is_featured=True).count()
    raise ValueError(resource)


def limit_of(store, resource):
    """(utilisés, maximum) ; maximum None = illimité. Formule expirée : maximum 0."""
    plan = plan_of(store)
    used = count(store, resource)
    if plan is None:
        return used, 0
    return used, getattr(plan, RESOURCES[resource][0])


def blocked(store, resource=None, feature=None, adding=1, user=None):
    """Message expliquant pourquoi l'action est refusée, ou None si elle est permise."""
    if store is None or _is_admin(user):
        return None
    plan = plan_of(store)
    if plan is None:
        return _expired_message(store)
    if feature:
        attr, label = FEATURES[feature]
        if getattr(plan, attr):
            return None
        up = _upgrade_for(attr, True)
        return f"{label} pas partie de la formule {plan.name}." + (f' Disponible avec {up.name}.' if up else '')
    attr, plural, singular = RESOURCES[resource]
    maximum = getattr(plan, attr)
    if maximum is None:
        return None
    used = count(store, resource)
    if used + adding <= maximum:
        return None
    up = _upgrade_for(attr, used + adding)
    if maximum == 0:
        text = f"La formule {plan.name} n'inclut pas les {plural}."
    else:
        text = f'La formule {plan.name} permet {maximum} {plural if maximum > 1 else singular} ({used} utilisé{"s" if used > 1 else ""}).'
    return text + (f' Passez à {up.name} pour aller plus loin.' if up else '')


def allows(store, feature, user=None):
    return blocked(store, feature=feature, user=user) is None


def requires_feature(feature, fallback):
    """Décorateur de vue : redirige vers `fallback` (nom d'URL) avec un message si la formule n'inclut pas `feature`."""
    from functools import wraps

    from django.contrib import messages
    from django.shortcuts import redirect

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                from store.access import acting_store
                reason = blocked(acting_store(request.user), feature=feature, user=request.user)
                if reason:
                    messages.error(request, reason)
                    return redirect(fallback)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def use_ai_question(store, user=None):
    """Décompte une question à l'assistant IA. Retourne le message de refus, ou None si elle est permise."""
    if store is None or _is_admin(user):
        return None
    sub = subscription_of(store)
    if not sub or not sub.is_active:
        return _expired_message(store)
    maximum = sub.plan.ai_questions_monthly
    period = timezone.localdate().strftime('%Y-%m')
    if sub.ai_period != period:
        sub.ai_period, sub.ai_questions_used = period, 0
    if maximum is not None and sub.ai_questions_used >= maximum:
        up = _upgrade_for('ai_questions_monthly', sub.ai_questions_used + 1)
        text = (f"L'assistant IA ne fait pas partie de la formule {sub.plan.name}." if maximum == 0 else
                f'Vous avez utilisé vos {maximum} questions à l\'assistant ce mois-ci (formule {sub.plan.name}).')
        return text + (f' Passez à {up.name} pour en avoir plus.' if up else '')
    sub.ai_questions_used += 1
    sub.save(update_fields=['ai_period', 'ai_questions_used'])
    return None


def commission_rate(store):
    """Commission sur les ventes en ligne (fraction, ex. 0.08) selon la formule du vendeur."""
    sub = subscription_of(store) if store else None
    if sub is not None:
        return Decimal(sub.plan.commission_rate) / 100
    return Decimal(str(settings.SALES_COMMISSION_RATE))


def upgrade_url():
    return reverse('billing:plans')


# ───────────────────────── Présentation des formules (pages publiques) ─────────────────────────
def _qty(value, singular, plural=None, unlimited='Illimité'):
    plural = plural or singular + 's'
    if value is None:
        return unlimited
    return f'{value:,} {plural if value > 1 else singular}'.replace(',', ' ')


def highlights(plan):
    """Points forts d'une formule, dans l'ordre où un vendeur les compare."""
    out = [_qty(plan.max_products, 'produit en ligne', 'produits en ligne', 'Produits illimités'),
           f'{_qty(plan.max_warehouses, "entrepôt", unlimited="Entrepôts illimités")} · '
           f'{_qty(plan.max_registers, "caisse", unlimited="caisses illimitées")}',
           _qty(plan.max_staff, 'employé', unlimited='Employés illimités')]
    if plan.max_suppliers != 0:
        out.append('Dropshipping : ' + _qty(plan.max_suppliers, 'fournisseur', unlimited='fournisseurs illimités'))
    if plan.ai_questions_monthly is None:
        out.append('Assistant IA illimité')
    elif plan.ai_questions_monthly:
        out.append(f'Assistant IA : {plan.ai_questions_monthly} questions par mois')
    if plan.can_whatsapp_inbox:
        out.append('Conversations WhatsApp')
    if plan.can_marketing:
        out.append('Campagnes marketing et ventes flash')
    if plan.can_b2b:
        out.append('Devis B2B et paie des employés')
    if plan.featured_products:
        out.append(_qty(plan.featured_products, 'produit mis en avant', 'produits mis en avant'))
    out += plan.feature_list
    return out


def comparison(plans):
    """Tableau comparatif : [{label, cells}] ; une cellule vaut un texte, True (inclus) ou False (non inclus)."""
    def num(value, zero=False):
        return 'Illimité' if value is None else (zero if value == 0 else f'{value:,}'.replace(',', ' '))

    rows = [
        ('Prix par mois', lambda p: 'Gratuit, 30 jours' if p.is_trial else f'{int(p.price_monthly):,} F'.replace(',', ' ')),
        ('Produits en ligne', lambda p: num(p.max_products)),
        ('Photos par produit', lambda p: str(p.max_images_per_product)),
        ('Entrepôts', lambda p: num(p.max_warehouses)),
        ('Caisses en magasin', lambda p: num(p.max_registers)),
        ('Employés', lambda p: num(p.max_staff)),
        ('Dropshipping (fournisseurs)', lambda p: num(p.max_suppliers, zero=False)),
        ("Assistant IA (questions par mois)", lambda p: num(p.ai_questions_monthly, zero=False)),
        ('Caisse, factures, stock, codes promo', lambda p: True),
        ('WhatsApp : confirmations, suivi, factures', lambda p: True),
        ('Conversations WhatsApp', lambda p: p.can_whatsapp_inbox),
        ('Campagnes marketing (e-mail, WhatsApp, Facebook)', lambda p: p.can_marketing),
        ('Ventes flash et statistiques avancées', lambda p: p.can_flash_deal and p.can_analytics),
        ('Réponses aux demandes de devis (B2B)', lambda p: p.can_b2b),
        ('Paie des employés', lambda p: p.can_payroll),
        ('Produits mis en avant', lambda p: num(p.featured_products, zero=False)),
        ('Support prioritaire', lambda p: p.can_priority_support),
    ]
    return [{'label': label, 'cells': [fn(p) for p in plans]} for label, fn in rows]


def usage(store):
    """Résumé pour « Mon abonnement » : [{label, used, max}]."""
    rows = []
    for resource, (attr, plural, _) in RESOURCES.items():
        used, maximum = limit_of(store, resource)
        rows.append({'key': resource, 'label': plural[:1].upper() + plural[1:], 'used': used, 'max': maximum})
    sub = subscription_of(store)
    if sub:
        period = timezone.localdate().strftime('%Y-%m')
        used = sub.ai_questions_used if sub.ai_period == period else 0
        rows.append({'key': 'ai', 'label': 'Questions à l\'assistant ce mois', 'used': used,
                     'max': sub.plan.ai_questions_monthly if sub.is_active else 0})
    return rows
