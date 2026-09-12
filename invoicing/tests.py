from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from store.models import Store
from .models import InvoiceSettings, Invoice, InvoiceItem

User = get_user_model()


class InvoiceSettingsTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(username='vendeur', password='testpass123', role='seller')
        self.store = Store.objects.create(owner=self.seller, name='Boutique Test')
        self.settings = InvoiceSettings.objects.create(store=self.store)

    def test_invoice_number_format_and_increment(self):
        """Les numéros de facture s'incrémentent : FAC-000001, FAC-000002..."""
        first = self.settings.get_next_invoice_number()
        second = self.settings.get_next_invoice_number()
        self.assertEqual(first, 'FAC-000001')
        self.assertEqual(second, 'FAC-000002')

    def test_custom_prefix(self):
        self.settings.invoice_prefix = 'PRO'
        self.assertEqual(self.settings.get_next_invoice_number(), 'PRO-000001')


class InvoiceTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(username='vendeur', password='testpass123', role='seller')
        self.customer = User.objects.create_user(username='client', password='testpass123', role='buyer')
        self.store = Store.objects.create(owner=self.seller, name='Boutique Test')
        self.settings = InvoiceSettings.objects.create(store=self.store)
        self.invoice = Invoice.objects.create(
            invoice_number='FAC-000001', store=self.store,
            customer=self.customer, customer_name='Client Test',
        )

    def test_item_total_with_discount(self):
        """Total ligne = quantité × prix − remise."""
        item = InvoiceItem.objects.create(
            invoice=self.invoice, description='Produit A',
            quantity=2, unit_price=Decimal('10000'), discount_percent=10,
        )
        self.assertEqual(item.total, Decimal('18000'))

    def test_calculate_totals_without_tva(self):
        InvoiceItem.objects.create(
            invoice=self.invoice, description='Produit A',
            quantity=2, unit_price=Decimal('10000'),
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.subtotal, Decimal('20000'))
        self.assertEqual(self.invoice.tva_amount, 0)
        self.assertEqual(self.invoice.total_amount, Decimal('20000'))

    def test_calculate_totals_with_tva(self):
        """TVA 19,25 % appliquée quand activée dans les paramètres."""
        self.settings.apply_tva = True
        self.settings.save()
        InvoiceItem.objects.create(
            invoice=self.invoice, description='Produit A',
            quantity=1, unit_price=Decimal('100000'),
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.tva_amount, Decimal('19250'))
        self.assertEqual(self.invoice.total_amount, Decimal('119250'))

    def test_mark_as_paid(self):
        self.invoice.mark_as_paid()
        self.assertEqual(self.invoice.status, 'paid')
        self.assertEqual(self.invoice.paid_date, date.today())

    def test_is_overdue(self):
        self.invoice.due_date = date.today() - timedelta(days=1)
        self.assertTrue(self.invoice.is_overdue)
        self.invoice.mark_as_paid()
        self.assertFalse(self.invoice.is_overdue)
