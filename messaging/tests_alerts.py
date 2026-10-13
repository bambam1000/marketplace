"""Alertes du haut de page : paiements (vendeur, acheteur, admin) et annonces de l'administration."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing import limits
from billing.models import SubscriptionPlan
from billing.subscriptions import request_plan
from orders.models import Order
from store.models import Store

from .models import Announcement

User = get_user_model()


class SiteAlertsTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(username='vendeur', password='testpass123', role='seller')
        self.store = Store.objects.create(owner=self.seller, name='Boutique Test')
        limits.start_trial(self.seller)
        self.buyer = User.objects.create_user(username='acheteur', password='testpass123', role='buyer')
        self.admin = User.objects.create_user(username='admin', password='testpass123', role='admin')
        self.home = reverse('home')

    def _titles(self, user=None, url=None):
        if user:
            self.client.force_login(user)
        response = self.client.get(url or self.home)
        self.assertEqual(response.status_code, 200)
        return [a['title'] for a in response.context['site_alerts']()]

    def test_visitor_sees_seller_invitation(self):
        """L'annonce « devenir vendeur » est créée par la migration 0009."""
        self.assertIn('Vous vendez ? Ouvrez votre boutique sur Comptoir', self._titles())

    def test_seller_sees_trial_on_public_pages(self):
        titles = self._titles(self.seller)
        self.assertTrue(any(t.startswith('Essai gratuit : encore') for t in titles))
        self.assertNotIn('Vous vendez ? Ouvrez votre boutique sur Comptoir', titles)  # déjà vendeur

    def test_expired_seller_alert(self):
        sub = limits.subscription_of(self.store)
        sub.end_date = timezone.now() - timedelta(minutes=1)
        sub.save()
        self.assertIn('Votre essai gratuit est terminé', self._titles(self.seller))

    def test_pending_payment_alert_everywhere(self):
        request_plan(self.seller, SubscriptionPlan.objects.get(slug='business'), 'monthly', 'momo', '677000000')
        self.assertIn('Formule Business : paiement en attente', self._titles(self.seller))
        dash = self.client.get(reverse('billing:my_subscription'))
        dash_titles = [a['title'] for a in dash.context['dash_alerts']()]
        self.assertIn('Formule Business : paiement en attente', dash_titles)
        self.assertFalse(any(t.startswith('Essai gratuit') for t in dash_titles))  # bandeau du dashboard déjà là

    def test_admin_sees_payments_to_validate(self):
        request_plan(self.seller, SubscriptionPlan.objects.get(slug='starter'), 'monthly', 'momo', '677000000')
        titles = self._titles(self.admin)
        self.assertIn("1 paiement d'abonnement à valider", titles)
        self.assertNotIn('Vous vendez ? Ouvrez votre boutique sur Comptoir', titles)

    def test_buyer_order_to_pay(self):
        order = Order.objects.create(buyer=self.buyer, payment_method='momo', subtotal=5000, total_amount=5000,
                                     shipping_name='Jean', shipping_phone='677000000', shipping_address='Rue 1',
                                     shipping_city='Douala')
        Order.objects.create(buyer=self.buyer, payment_method='cash', subtotal=1000, total_amount=1000,
                             shipping_name='Jean', shipping_phone='677000000', shipping_address='Rue 1', shipping_city='Douala')
        self.assertIn(f'Commande {order.order_number} en attente de paiement', self._titles(self.buyer))
        order.is_paid = True
        order.save()
        self.assertFalse(any('en attente de paiement' in t for t in self._titles(self.buyer)))

    def test_announcement_audience_and_dates(self):
        Announcement.objects.create(title='Pour les vendeurs', audience='sellers')
        Announcement.objects.create(title='Plus tard', audience='all', starts_at=timezone.now() + timedelta(days=1))
        Announcement.objects.create(title='Finie', audience='all', ends_at=timezone.now() - timedelta(days=1))
        visitor = self._titles()
        self.assertNotIn('Pour les vendeurs', visitor)
        self.assertNotIn('Plus tard', visitor)
        self.assertNotIn('Finie', visitor)
        self.assertIn('Pour les vendeurs', self._titles(self.seller))

    def test_home_renders_alert_bar(self):
        response = self.client.get(self.home)
        self.assertContains(response, 'class="sa sa-promo"')
        self.assertContains(response, 'Devenir vendeur')

    def test_admin_creates_announcement(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('dashboard:admin_announcements'), {
            'title': 'Livraison offerte', 'message': 'Ce week-end', 'link_url': '/produits/', 'audience': 'all',
            'tone': 'success', 'priority': '5', 'is_active': 'on', 'dismissible': 'on'})
        a = Announcement.objects.get(title='Livraison offerte')
        self.assertEqual((a.tone, a.priority, a.created_by), ('success', 5, self.admin))
        self.client.logout()
        self.assertEqual(self._titles()[0], 'Livraison offerte')  # priorité la plus haute en premier
        self.client.force_login(self.admin)
        self.client.post(reverse('dashboard:admin_announcement_action', args=[a.pk, 'toggle']))
        self.client.logout()
        self.assertNotIn('Livraison offerte', self._titles())

    def test_admin_rejects_unsafe_link(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('dashboard:admin_announcements'), {'title': 'X', 'link_url': 'javascript:alert(1)', 'audience': 'all'})
        self.assertFalse(Announcement.objects.filter(title='X').exists())
