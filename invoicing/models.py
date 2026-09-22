from decimal import Decimal

from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class InvoiceSettings(models.Model):
    """Paramètres de facturation par boutique"""
    store = models.OneToOneField('store.Store', on_delete=models.CASCADE, related_name='invoice_settings')

    # Informations légales
    company_name = models.CharField(max_length=200, blank=True)
    tax_id = models.CharField(max_length=100, blank=True, verbose_name="Numéro de contribuable")
    registration_number = models.CharField(max_length=100, blank=True, verbose_name="RCCM")

    # Coordonnées
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, default='Douala')
    country = models.CharField(max_length=100, default='Cameroun')
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    # Paramètres fiscaux
    apply_tva = models.BooleanField(default=False, verbose_name="Appliquer la TVA")
    tva_rate = models.DecimalField(max_digits=5, decimal_places=2, default=19.25, verbose_name="Taux TVA (%)")

    # Numérotation
    invoice_prefix = models.CharField(max_length=10, default='FAC', help_text="Préfixe des numéros de facture")
    next_invoice_number = models.IntegerField(default=1)

    # Personnalisation
    logo = models.ImageField(upload_to='invoices/logos/', blank=True, null=True)
    header_text = models.TextField(blank=True, help_text="Texte d'en-tête")
    footer_text = models.TextField(blank=True, help_text="Mentions légales en pied de page")
    payment_terms = models.TextField(blank=True, default="Paiement à la livraison", verbose_name="Conditions de paiement")
    bank_details = models.TextField(blank=True, verbose_name="Coordonnées bancaires")

    # Signature
    signature = models.ImageField(upload_to='invoices/signatures/', blank=True, null=True)
    signature_name = models.CharField(max_length=100, blank=True)
    signature_title = models.CharField(max_length=100, blank=True, help_text="Ex: Gérant, Directeur")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Paramètres - {self.store.name}"

    def get_next_invoice_number(self):
        """Génère le prochain numéro de facture"""
        number = f"{self.invoice_prefix}-{self.next_invoice_number:06d}"
        self.next_invoice_number += 1
        self.save()
        return number


class Invoice(models.Model):
    """Facture"""
    INVOICE_TYPE_CHOICES = [
        ('proforma', 'Pro Forma (Devis)'),
        ('standard', 'Facture Standard'),
        ('credit_note', 'Avoir (Remboursement)'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Brouillon'),
        ('sent', 'Envoyée'),
        ('paid', 'Payée'),
        ('cancelled', 'Annulée'),
    ]

    # Références
    invoice_number = models.CharField(max_length=50, unique=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='invoices')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')

    # Type et statut
    invoice_type = models.CharField(max_length=20, choices=INVOICE_TYPE_CHOICES, default='standard')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Client
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='invoices')
    customer_name = models.CharField(max_length=200)
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_address = models.TextField(blank=True)

    # Dates
    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)

    # Montants
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tva_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Montant TVA")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Informations supplémentaires
    notes = models.TextField(blank=True, help_text="Notes pour le client")
    internal_notes = models.TextField(blank=True, help_text="Notes internes (non visibles sur la facture)")
    payment_method = models.CharField(max_length=50, blank=True)

    # Fichier PDF
    pdf_file = models.FileField(upload_to='invoices/pdfs/', blank=True, null=True)

    # Tracking
    sent_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.invoice_number} - {self.customer_name}"

    def calculate_totals(self):
        """Calcule les totaux de la facture"""
        self.subtotal = sum(item.total for item in self.items.all())

        # TVA
        settings = self.store.invoice_settings
        if settings.apply_tva:
            self.tva_amount = self.subtotal * (Decimal(str(settings.tva_rate)) / Decimal('100'))
        else:
            self.tva_amount = 0

        # Total
        self.total_amount = self.subtotal + self.tva_amount - self.discount_amount + self.shipping_amount
        self.save()

    def mark_as_paid(self):
        """Marque la facture comme payée"""
        self.status = 'paid'
        self.paid_date = timezone.now().date()
        self.save()

    @property
    def is_overdue(self):
        """Vérifie si la facture est en retard"""
        if self.status == 'paid' or not self.due_date:
            return False
        return timezone.now().date() > self.due_date


class InvoiceItem(models.Model):
    """Ligne de facture"""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.SET_NULL, null=True, blank=True)

    # Description
    description = models.CharField(max_length=500)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Options
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    order = models.IntegerField(default=0, help_text="Ordre d'affichage")

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    def save(self, *args, **kwargs):
        # Calculer le total
        subtotal = self.quantity * self.unit_price
        discount = subtotal * (Decimal(str(self.discount_percent)) / Decimal('100'))
        self.total = subtotal - discount
        super().save(*args, **kwargs)

        # Recalculer les totaux de la facture
        self.invoice.calculate_totals()


class DeliveryNote(models.Model):
    """Bon de livraison"""
    delivery_number = models.CharField(max_length=50, unique=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='delivery_notes')
    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='delivery_notes')

    delivery_date = models.DateField(default=timezone.now)
    delivered_by = models.CharField(max_length=200, blank=True)
    received_by = models.CharField(max_length=200, blank=True)

    notes = models.TextField(blank=True)
    signature_image = models.ImageField(upload_to='invoices/delivery_signatures/', blank=True, null=True)

    pdf_file = models.FileField(upload_to='invoices/delivery_notes/', blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"BL-{self.delivery_number}"


class PaymentReceipt(models.Model):
    """Reçu de paiement"""
    receipt_number = models.CharField(max_length=50, unique=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payment_receipts')

    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=timezone.now)
    payment_method = models.CharField(max_length=50)
    transaction_reference = models.CharField(max_length=200, blank=True)

    notes = models.TextField(blank=True)
    pdf_file = models.FileField(upload_to='invoices/receipts/', blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reçu-{self.receipt_number}"
