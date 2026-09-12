from django.test import TestCase
from django.contrib.auth import get_user_model

from catalog.models import Category, Product
from store.models import Store
from .models import Order, OrderItem

User = get_user_model()


class OrderModelTests(TestCase):
    """Tests sur le cycle de vie et les montants des commandes."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            username='acheteur', password='testpass123', role='buyer'
        )
        self.seller = User.objects.create_user(
            username='vendeur', password='testpass123', role='seller'
        )
        self.store = Store.objects.create(owner=self.seller, name='Boutique Test')
        self.category = Category.objects.create(name='Électronique', slug='electronique')
        self.product = Product.objects.create(
            store=self.store, category=self.category,
            name='Smartphone X', description='Un smartphone',
            price=150000, stock=10,
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            payment_method='momo',
            subtotal=300000,
            shipping_cost=2000,
            total_amount=302000,
            shipping_name='Jean Dupont',
            shipping_phone='677123456',
            shipping_address='123 rue Principale',
            shipping_city='Douala',
        )

    def test_order_number_generated_automatically(self):
        """Le numéro de commande est généré au format ALB-XXXXXXXX."""
        self.assertTrue(self.order.order_number.startswith('ALB-'))
        self.assertEqual(len(self.order.order_number), 12)

    def test_order_number_unique(self):
        order2 = Order.objects.create(
            buyer=self.buyer, payment_method='om',
            subtotal=1000, shipping_cost=0, total_amount=1000,
            shipping_name='A', shipping_phone='600000000',
            shipping_address='B', shipping_city='Yaoundé',
        )
        self.assertNotEqual(self.order.order_number, order2.order_number)

    def test_default_status_is_pending(self):
        self.assertEqual(self.order.status, 'pending')
        self.assertFalse(self.order.is_paid)

    def test_progress_percent_follows_status(self):
        self.assertEqual(self.order.progress_percent, 20)
        self.order.status = 'delivered'
        self.assertEqual(self.order.progress_percent, 100)
        self.order.status = 'cancelled'
        self.assertEqual(self.order.progress_percent, 0)

    def test_order_item_subtotal(self):
        """Le sous-total d'une ligne = prix × quantité."""
        item = OrderItem.objects.create(
            order=self.order, product=self.product, store=self.store,
            quantity=2, price=150000,
        )
        self.assertEqual(item.subtotal, 300000)

    def test_total_matches_items_plus_shipping(self):
        """Cohérence : total = somme des lignes + livraison."""
        OrderItem.objects.create(
            order=self.order, product=self.product, store=self.store,
            quantity=2, price=150000,
        )
        items_total = sum(i.subtotal for i in self.order.items.all())
        self.assertEqual(self.order.total_amount, items_total + self.order.shipping_cost)
