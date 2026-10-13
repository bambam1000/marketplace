"""Grille des formules (essai gratuit + 4 formules payantes) et essai pour les vendeurs sans abonnement."""
from datetime import timedelta

from django.db import migrations
from django.utils import timezone

# Limites : None = illimité, 0 = non inclus
PLANS = [
    dict(slug='essai', name='Essai gratuit', order=0, price_monthly=0, price_yearly=None, is_trial=True, trial_days=30,
         commission_rate=10, max_products=15, max_images_per_product=2, max_warehouses=1, max_registers=1, max_staff=1,
         max_suppliers=0, ai_questions_monthly=20, can_marketing=False, can_b2b=False, can_payroll=False,
         can_whatsapp_inbox=True, can_dropship_auto=False, can_flash_deal=False, can_boost=False, can_analytics=False,
         can_priority_support=False, can_custom_store=False, boost_credits_monthly=0, featured_products=0,
         badge_level='basic', is_popular=False,
         description="30 jours pour tester la boutique en ligne, la caisse, les factures et WhatsApp sur un petit catalogue.",
         features='Sans moyen de paiement\nSupport par e-mail'),
    dict(slug='starter', name='Starter', order=1, price_monthly=3000, price_yearly=30000, is_trial=False,
         commission_rate=12, max_products=50, max_images_per_product=3, max_warehouses=1, max_registers=1, max_staff=2,
         max_suppliers=1, ai_questions_monthly=0, can_marketing=False, can_b2b=False, can_payroll=False,
         can_whatsapp_inbox=False, can_dropship_auto=False, can_flash_deal=False, can_boost=False, can_analytics=False,
         can_priority_support=False, can_custom_store=False, boost_credits_monthly=0, featured_products=0,
         badge_level='basic', is_popular=False,
         description="Pour démarrer : un magasin, une caisse et votre boutique en ligne.",
         features='Support par e-mail'),
    dict(slug='business', name='Business', order=2, price_monthly=8000, price_yearly=80000, is_trial=False,
         commission_rate=8, max_products=300, max_images_per_product=4, max_warehouses=3, max_registers=3, max_staff=5,
         max_suppliers=3, ai_questions_monthly=100, can_marketing=True, can_b2b=True, can_payroll=True,
         can_whatsapp_inbox=True, can_dropship_auto=True, can_flash_deal=True, can_boost=True, can_analytics=True,
         can_priority_support=False, can_custom_store=False, boost_credits_monthly=2, featured_products=1,
         badge_level='silver', is_popular=True,
         description="Pour les PME : plusieurs magasins, une équipe, le marketing et les ventes aux professionnels.",
         features='Support par e-mail prioritaire'),
    dict(slug='premium', name='Premium', order=3, price_monthly=15000, price_yearly=150000, is_trial=False,
         commission_rate=5, max_products=1500, max_images_per_product=4, max_warehouses=10, max_registers=None, max_staff=15,
         max_suppliers=10, ai_questions_monthly=500, can_marketing=True, can_b2b=True, can_payroll=True,
         can_whatsapp_inbox=True, can_dropship_auto=True, can_flash_deal=True, can_boost=True, can_analytics=True,
         can_priority_support=True, can_custom_store=True, boost_credits_monthly=5, featured_products=5,
         badge_level='gold', is_popular=False,
         description="Pour les réseaux de magasins et le dropshipping à grande échelle.",
         features='Support WhatsApp prioritaire\nBadge Gold'),
    dict(slug='enterprise', name='Entreprise', order=4, price_monthly=35000, price_yearly=350000, is_trial=False,
         commission_rate=3, max_products=None, max_images_per_product=4, max_warehouses=None, max_registers=None,
         max_staff=None, max_suppliers=None, ai_questions_monthly=None, can_marketing=True, can_b2b=True, can_payroll=True,
         can_whatsapp_inbox=True, can_dropship_auto=True, can_flash_deal=True, can_boost=True, can_analytics=True,
         can_priority_support=True, can_custom_store=True, boost_credits_monthly=15, featured_products=15,
         badge_level='platinum', is_popular=False,
         description="Sans limite, avec un interlocuteur dédié et la formation de votre équipe.",
         features='Interlocuteur dédié\nBadge Platinum\nFormation de votre équipe'),
]


def apply_grid(apps, schema_editor):
    Plan = apps.get_model('billing', 'SubscriptionPlan')
    Subscription = apps.get_model('billing', 'Subscription')
    Store = apps.get_model('store', 'Store')
    for data in PLANS:
        data = dict(data)
        slug = data.pop('slug')
        Plan.objects.update_or_create(slug=slug, defaults={**data, 'is_active': True})
    trial = Plan.objects.get(slug='essai')
    end = timezone.now() + timedelta(days=trial.trial_days)
    owners = set(Store.objects.values_list('owner_id', flat=True))
    have = set(Subscription.objects.values_list('user_id', flat=True))
    for user_id in owners - have:
        Subscription.objects.create(user_id=user_id, plan=trial, status='trial', end_date=end, auto_renew=False)


class Migration(migrations.Migration):
    dependencies = [
        ('billing', '0009_plan_limits'),
        ('store', '0009_plan_limits'),
    ]
    operations = [migrations.RunPython(apply_grid, migrations.RunPython.noop)]
