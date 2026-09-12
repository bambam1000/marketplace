from datetime import timedelta

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import SubscriptionPlan, Subscription, SubscriptionPayment

User = get_user_model()


class SubscriptionPlanTests(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name='Gold', slug='gold',
            price_monthly=10000, price_yearly=100000,
            features='Boost\nAnalytics\nSupport prioritaire',
        )

    def test_feature_list_splits_lines(self):
        self.assertEqual(self.plan.feature_list, ['Boost', 'Analytics', 'Support prioritaire'])

    def test_yearly_savings(self):
        """Économie annuelle = 12 × mensuel − prix annuel."""
        self.assertEqual(self.plan.yearly_savings, 20000)

    def test_yearly_savings_without_yearly_price(self):
        plan = SubscriptionPlan.objects.create(name='Basic', slug='basic', price_monthly=5000)
        self.assertEqual(plan.yearly_savings, 0)


class SubscriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='vendeur', password='testpass123', role='seller')
        self.plan = SubscriptionPlan.objects.create(
            name='Gold', slug='gold', price_monthly=10000, price_yearly=100000,
        )

    def _make_subscription(self, days=30, status='active', cycle='monthly'):
        return Subscription.objects.create(
            user=self.user, plan=self.plan, status=status, billing_cycle=cycle,
            end_date=timezone.now() + timedelta(days=days),
        )

    def test_active_subscription_is_active(self):
        sub = self._make_subscription(days=30)
        self.assertTrue(sub.is_active)
        self.assertGreater(sub.days_remaining, 0)

    def test_expired_subscription_is_not_active(self):
        sub = self._make_subscription(days=-1)
        self.assertFalse(sub.is_active)
        self.assertEqual(sub.days_remaining, 0)

    def test_cancelled_subscription_is_not_active(self):
        sub = self._make_subscription(days=30, status='cancelled')
        self.assertFalse(sub.is_active)

    def test_current_amount_monthly(self):
        sub = self._make_subscription(cycle='monthly')
        self.assertEqual(sub.current_amount, 10000)

    def test_current_amount_yearly(self):
        sub = self._make_subscription(cycle='yearly')
        self.assertEqual(sub.current_amount, 100000)


class SubscriptionPaymentTests(TestCase):
    def test_payment_default_status_pending(self):
        user = User.objects.create_user(username='vendeur2', password='testpass123', role='seller')
        plan = SubscriptionPlan.objects.create(name='Gold', slug='gold', price_monthly=10000)
        sub = Subscription.objects.create(
            user=user, plan=plan, status='active',
            end_date=timezone.now() + timedelta(days=30),
        )
        payment = SubscriptionPayment.objects.create(
            subscription=sub, amount=10000, payment_method='momo',
        )
        self.assertEqual(payment.status, 'pending')
