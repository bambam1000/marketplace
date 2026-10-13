"""Formules : essai gratuit, limites appliquées, commission, demande et validation du paiement, expiration."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Product
from inventory.models import Warehouse
from store.models import Store

from . import limits
from .models import Subscription, SubscriptionPayment, SubscriptionPlan
from .subscriptions import grant, request_plan, revoke, run_daily, validate

User = get_user_model()


class PlanLimitsTests(TestCase):
    """La grille des formules est créée par la migration 0010 : essai, starter, business, premium, enterprise."""

    def setUp(self):
        self.seller = User.objects.create_user(username='vendeur', password='testpass123', role='seller')
        self.store = Store.objects.create(owner=self.seller, name='Boutique Test')
        Warehouse.objects.create(store=self.store, name='Principal', code='MAIN', is_default=True)
        self.category = Category.objects.create(name='Divers', slug='divers')
        self.admin = User.objects.create_user(username='admin', password='testpass123', role='admin')

    def _products(self, n):
        Product.objects.bulk_create([Product(store=self.store, category=self.category, name=f'P{i}', slug=f'p-{i}',
                                             description='x', price=1000, stock=1) for i in range(n)])

    def _set_plan(self, slug, days=30):
        sub = limits.start_trial(self.seller)
        sub.plan, sub.status = SubscriptionPlan.objects.get(slug=slug), 'active'
        sub.end_date = timezone.now() + timedelta(days=days)
        sub.save()
        return sub

    # ── Essai ──
    def test_new_seller_gets_30_day_trial(self):
        sub = limits.subscription_of(self.store)
        self.assertTrue(sub.plan.is_trial)
        self.assertEqual(sub.status, 'trial')
        self.assertIn(sub.days_remaining, (29, 30))

    def test_trial_product_limit(self):
        self._products(15)
        reason = limits.blocked(self.store, 'products')
        self.assertIn('15 produits', reason)
        self.assertIn('Starter', reason)  # formule la moins chère qui permet plus
        self.assertIsNone(limits.blocked(self.store, 'products', user=self.admin))  # l'admin n'est jamais limité

    def test_product_page_refuses_new_product_over_limit(self):
        self._products(15)
        self.client.force_login(self.seller)
        response = self.client.get(reverse('dashboard:product_add'))
        self.assertRedirects(response, reverse('dashboard:products'), fetch_redirect_response=False)

    def test_warehouse_and_supplier_limits(self):
        self.client.force_login(self.seller)
        self.client.post(reverse('dashboard:warehouses'), {'name': 'Deuxième', 'code': 'W2'})
        self.client.post(reverse('dashboard:warehouses'), {'name': 'Fournisseur', 'code': 'F1', 'kind': 'dropship'})
        self.assertEqual(self.store.warehouses.count(), 1)  # essai : 1 entrepôt, pas de dropshipping
        self._set_plan('business')
        self.client.post(reverse('dashboard:warehouses'), {'name': 'Fournisseur', 'code': 'F1', 'kind': 'dropship'})
        self.assertTrue(self.store.warehouses.filter(kind='dropship').exists())

    def test_features_by_plan(self):
        self.assertFalse(limits.allows(self.store, 'payroll'))
        self.assertTrue(limits.allows(self.store, 'whatsapp_inbox'))
        self._set_plan('starter')
        self.assertFalse(limits.allows(self.store, 'marketing'))
        self.assertIn('Business', limits.blocked(self.store, feature='marketing'))
        self._set_plan('business')
        self.assertTrue(limits.allows(self.store, 'marketing'))

    def test_marketing_page_redirects_without_feature(self):
        self._set_plan('starter')
        self.client.force_login(self.seller)
        response = self.client.get(reverse('marketing:campaign_create'))
        self.assertRedirects(response, reverse('marketing:dashboard'), fetch_redirect_response=False)

    def test_ai_quota(self):
        for _ in range(20):
            self.assertIsNone(limits.use_ai_question(self.store))
        self.assertIn('20 questions', limits.use_ai_question(self.store))
        self._set_plan('starter')
        self.assertIn("ne fait pas partie", limits.use_ai_question(self.store))

    def test_no_commission_on_sales(self):
        """Comptoir se rémunère par l'abonnement : 0 % de commission, quelle que soit la formule."""
        for slug in ('starter', 'business', 'premium', 'enterprise'):
            self._set_plan(slug)
            self.assertEqual(limits.commission_rate(self.store), 0)
        self.assertIsNone(limits.blocked(self.store, 'products', adding=10 ** 6))  # Entreprise : illimité

    # ── Expiration ──
    def test_expired_trial_blocks_and_hides_store(self):
        sub = limits.subscription_of(self.store)
        sub.end_date = timezone.now() - timedelta(minutes=1)
        sub.save()
        self.assertIn('essai gratuit est terminé', limits.blocked(self.store, 'products'))
        reminders, expired = run_daily()
        self.assertEqual(expired, 1)
        self.store.refresh_from_db()
        self.assertFalse(self.store.is_active)
        self.assertTrue(self.store.billing_suspended)

    def test_reminders_sent_once(self):
        sub = limits.subscription_of(self.store)
        sub.end_date = timezone.now() + timedelta(days=5)
        sub.save()
        self.assertEqual(run_daily(), (1, 0))   # rappel J-7
        self.assertEqual(run_daily(), (0, 0))   # pas deux fois
        sub.refresh_from_db()
        sub.end_date = timezone.now() + timedelta(days=1)
        sub.save()
        self.assertEqual(run_daily(), (1, 0))   # rappel J-2

    # ── Souscription ──
    def test_subscribe_does_not_activate_without_validation(self):
        self.client.force_login(self.seller)
        self.client.post(reverse('billing:subscribe', args=['business']),
                         {'billing_cycle': 'monthly', 'payment_method': 'momo', 'payer_phone': '677000000'})
        sub = Subscription.objects.get(user=self.seller)
        self.assertTrue(sub.plan.is_trial)  # toujours en essai
        payment = SubscriptionPayment.objects.get(subscription=sub)
        self.assertEqual((payment.status, payment.plan.slug, payment.amount), ('pending', 'business', 8000))

    def test_admin_validation_activates_plan_and_restores_store(self):
        self.store.is_active, self.store.billing_suspended = False, True
        self.store.save()
        payment = request_plan(self.seller, SubscriptionPlan.objects.get(slug='premium'), 'yearly', 'om', '699000000')
        validate(payment, self.admin)
        sub = Subscription.objects.get(user=self.seller)
        self.assertEqual((sub.plan.slug, sub.status, sub.billing_cycle), ('premium', 'active', 'yearly'))
        self.assertGreaterEqual(sub.days_remaining, 364)
        self.store.refresh_from_db()
        self.assertTrue(self.store.is_active)
        self.assertFalse(self.store.billing_suspended)
        validate(payment, self.admin)  # déjà validé : sans effet
        self.assertEqual(SubscriptionPayment.objects.filter(status='completed').count(), 1)

    def test_admin_page_validates_payment(self):
        payment = request_plan(self.seller, SubscriptionPlan.objects.get(slug='starter'), 'monthly', 'momo', '677000000')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('dashboard:admin_subscriptions')), 'Paiements à valider')
        self.client.post(reverse('dashboard:admin_subscription_payment', args=[payment.pk, 'validate']))
        self.assertEqual(Subscription.objects.get(user=self.seller).plan.slug, 'starter')

    def test_public_pages_render(self):
        response = self.client.get(reverse('seller_landing'))
        self.assertContains(response, 'Essai gratuit')
        self.assertContains(response, 'Comparer toutes les formules')
        self.client.force_login(self.seller)
        self.assertContains(self.client.get(reverse('billing:plans')), 'Business')
        self.assertContains(self.client.get(reverse('billing:my_subscription')), 'Utilisation de votre formule')
        self.assertContains(self.client.get(reverse('dashboard:index')), 'Essai gratuit : encore')

    # ── Accès offert par l'administrateur ──
    def test_admin_grants_access_without_payment(self):
        self.store.is_active, self.store.billing_suspended = False, True
        self.store.save()
        self.client.force_login(self.admin)
        plan = SubscriptionPlan.objects.get(slug='premium')
        self.client.post(reverse('dashboard:admin_user_access', args=[self.seller.pk]),
                         {'plan': plan.pk, 'duration': '', 'note': 'Partenaire'})
        sub = Subscription.objects.get(user=self.seller)
        self.assertEqual((sub.plan.slug, sub.status, sub.granted_by, sub.grant_note), ('premium', 'active', self.admin, 'Partenaire'))
        self.assertEqual(sub.end_date.year, 2099)  # sans limite de durée
        self.assertFalse(SubscriptionPayment.objects.filter(subscription=sub).exists())  # aucun paiement
        self.store.refresh_from_db()
        self.assertTrue(self.store.is_active)
        self.assertTrue(limits.allows(self.store, 'marketing'))
        self.assertEqual(run_daily(), (0, 0))  # ni rappel ni expiration
        self.assertContains(self.client.get(reverse('dashboard:user_detail', args=[self.seller.pk])), 'Offert')

    def test_grant_with_duration_and_revoke(self):
        sub = grant(self.seller, SubscriptionPlan.objects.get(slug='business'), 90, self.admin)
        self.assertIn(sub.days_remaining, (89, 90))
        revoke(sub, self.admin)
        self.assertIsNotNone(limits.blocked(self.store, 'products'))
        self.store.refresh_from_db()
        self.assertFalse(self.store.is_active)

    def test_payment_numbers_shown_to_seller(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('dashboard:admin_subscriptions'),
                         {'form': 'pay_settings', 'momo_number': '677112233', 'momo_name': 'Comptoir SARL'})
        self.client.force_login(self.seller)
        response = self.client.get(reverse('billing:subscribe', args=['business']))
        self.assertContains(response, '677112233')
        self.assertContains(response, 'Comptoir SARL')

    def test_dry_run_changes_nothing(self):
        sub = limits.subscription_of(self.store)
        sub.end_date = timezone.now() - timedelta(minutes=1)
        sub.save()
        report = []
        self.assertEqual(run_daily(dry_run=True, report=report), (0, 1))
        self.assertEqual(len(report), 1)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'trial')
        self.store.refresh_from_db()
        self.assertTrue(self.store.is_active)
