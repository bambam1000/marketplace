from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
import uuid


class POSSession(models.Model):
    """Session de caisse - ouverture/fermeture"""
    STATUS_CHOICES = [
        ('open', 'Ouverte'),
        ('closed', 'Fermée'),
    ]

    session_number = models.CharField(max_length=50, unique=True, editable=False)
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='pos_sessions')
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cashier_sessions')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')

    # Fonds de caisse
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Fond de caisse d'ouverture")
    closing_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Totaux
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_card = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_mobile_money = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    sales_count = models.IntegerField(default=0)

    # Dates
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-opened_at']

    def __str__(self):
        return f"{self.session_number} - {self.cashier.display_name}"

    def save(self, *args, **kwargs):
        if not self.session_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count = POSSession.objects.filter(opened_at__date=timezone.now().date()).count() + 1
            self.session_number = f"SESS-{date_str}-{count:03d}"
        super().save(*args, **kwargs)

    def close_session(self, closing_cash):
        """Ferme la session de caisse"""
        self.status = 'closed'
        self.closed_at = timezone.now()
        self.closing_cash = Decimal(str(closing_cash))

        # Calculer l'écart
        self.expected_cash = self.opening_cash + self.total_cash
        self.cash_difference = self.closing_cash - self.expected_cash

        self.save()

    def calculate_totals(self):
        """Recalcule les totaux de la session"""
        sales = self.sales.filter(status='completed')

        self.total_sales = sum(sale.total_amount for sale in sales)
        self.total_cash = sum(sale.cash_amount for sale in sales)
        self.total_card = sum(sale.card_amount for sale in sales)
        self.total_mobile_money = sum(sale.mobile_money_amount for sale in sales)
        self.sales_count = sales.count()

        self.save()


class POSSale(models.Model):
    """Vente en caisse"""
    STATUS_CHOICES = [
        ('pending', 'En cours'),
        ('completed', 'Complétée'),
        ('cancelled', 'Annulée'),
        ('refunded', 'Remboursée'),
    ]

    sale_number = models.CharField(max_length=50, unique=True, editable=False)
    session = models.ForeignKey(POSSession, on_delete=models.CASCADE, related_name='sales')
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pos_sales')

    # Client (optionnel)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_purchases')
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Montants
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Paiements multiples
    cash_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    card_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mobile_money_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_tendered = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Montant reçu")
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Monnaie rendue")

    # Documents
    invoice = models.ForeignKey('invoicing.Invoice', on_delete=models.SET_NULL, null=True, blank=True)
    receipt_printed = models.BooleanField(default=False)
    invoice_printed = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.sale_number} - {self.total_amount} F"

    def save(self, *args, **kwargs):
        if not self.sale_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count = POSSale.objects.filter(created_at__date=timezone.now().date()).count() + 1
            self.sale_number = f"SALE-{date_str}-{count:05d}"
        super().save(*args, **kwargs)

    def calculate_totals(self):
        """Calcule les totaux de la vente"""
        self.subtotal = sum(item.total for item in self.items.all())

        # Remise
        if self.discount_percent > 0:
            self.discount_amount = self.subtotal * (self.discount_percent / 100)

        # TVA (si applicable)
        settings = self.store.invoice_settings
        if hasattr(settings, 'apply_tva') and settings.apply_tva:
            taxable = self.subtotal - self.discount_amount
            self.tax_amount = taxable * (settings.tva_rate / 100)
        else:
            self.tax_amount = 0

        # Total
        self.total_amount = self.subtotal - self.discount_amount + self.tax_amount

        self.save()

    def complete_sale(self):
        """Finalise la vente"""
        self.status = 'completed'
        self.completed_at = timezone.now()

        # Calculer la monnaie
        total_paid = self.cash_amount + self.card_amount + self.mobile_money_amount
        if total_paid >= self.total_amount:
            self.change_amount = total_paid - self.total_amount

        self.save()

        # Mettre à jour les stocks
        for item in self.items.all():
            if item.product:
                item.product.stock -= item.quantity
                item.product.orders_count += item.quantity
                item.product.save()

        # Mettre à jour la session
        self.session.calculate_totals()

    def refund(self):
        """Rembourse la vente"""
        if self.status != 'completed':
            return False

        self.status = 'refunded'
        self.save()

        # Restaurer les stocks
        for item in self.items.all():
            if item.product:
                item.product.stock += item.quantity
                item.product.save()

        # Mettre à jour la session
        self.session.calculate_totals()

        return True


class POSSaleItem(models.Model):
    """Ligne de vente"""
    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.SET_NULL, null=True, blank=True)

    # Détails
    product_name = models.CharField(max_length=500)
    product_sku = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Options
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.product_name} x{self.quantity}"

    def save(self, *args, **kwargs):
        # Calculer le total
        subtotal = float(self.quantity) * float(self.unit_price)
        discount = subtotal * (float(self.discount_percent) / 100)
        self.total = Decimal(str(subtotal - discount))

        super().save(*args, **kwargs)

        # Recalculer les totaux de la vente
        self.sale.calculate_totals()


class CashMovement(models.Model):
    """Mouvements de caisse (entrées/sorties)"""
    TYPE_CHOICES = [
        ('in', 'Entrée'),
        ('out', 'Sortie'),
    ]

    CATEGORY_CHOICES = [
        ('opening', 'Fond de caisse'),
        ('sale', 'Vente'),
        ('refund', 'Remboursement'),
        ('expense', 'Dépense'),
        ('bank_deposit', 'Dépôt banque'),
        ('other', 'Autre'),
    ]

    session = models.ForeignKey(POSSession, on_delete=models.CASCADE, related_name='cash_movements')
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=500)
    reference = models.CharField(max_length=100, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.type == 'in' else '-'
        return f"{sign}{self.amount} F - {self.get_category_display()}"


class POSProduct(models.Model):
    """Produit configuré pour le POS (codes-barres, prix rapides)"""
    product = models.OneToOneField('catalog.Product', on_delete=models.CASCADE, related_name='pos_config')

    barcode = models.CharField(max_length=100, unique=True, blank=True, null=True)
    quick_button = models.BooleanField(default=False, help_text="Afficher comme bouton rapide")
    button_order = models.IntegerField(default=0)
    button_color = models.CharField(max_length=20, default='#ff6a00')

    # Prix rapides
    allow_price_override = models.BooleanField(default=False)
    min_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Unités de vente
    sell_by_weight = models.BooleanField(default=False)
    weight_unit = models.CharField(max_length=10, default='kg', choices=[('kg', 'Kilogramme'), ('g', 'Gramme')])

    class Meta:
        ordering = ['button_order', 'product__name']

    def __str__(self):
        return f"POS: {self.product.name}"
