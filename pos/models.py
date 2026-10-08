from django.db import models
from django.db.models import Q, Sum
from django.conf import settings
from django.utils import timezone
from decimal import Decimal

from config.numbering import save_with_reference

PAYMENT_METHODS = [
    ('cash', 'Espèces'),
    ('card', 'Carte bancaire'),
    ('mobile_money', 'Mobile Money'),
]

# Coupures du franc CFA, pour le comptage du tiroir à la fermeture
CFA_DENOMINATIONS = [10000, 5000, 2000, 1000, 500, 250, 200, 100, 50, 25, 10, 5]


class CashRegister(models.Model):
    """Caisse physique d'un entrepôt (point de vente). Un entrepôt peut avoir plusieurs caisses ;
    chaque caisse n'a qu'une session ouverte à la fois."""
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='cash_registers')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.CASCADE, related_name='cash_registers')
    name = models.CharField(max_length=60, help_text='Ex : Caisse 1, Comptoir entrée')
    code = models.CharField(max_length=20, blank=True, help_text='Affiché sur les tickets. Ex : DLA-C1')
    default_opening_cash = models.DecimalField(max_digits=12, decimal_places=0, default=0,
                                               help_text="Fond de caisse proposé à l'ouverture")
    max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=10,
                                               help_text='Remise maximale accordée par un caissier (un responsable peut aller au-delà)')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['warehouse__name', 'name']
        constraints = [models.UniqueConstraint(fields=['warehouse', 'name'], name='pos_register_unique_name')]

    def __str__(self):
        return f"{self.name} — {self.warehouse.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            n = CashRegister.objects.filter(warehouse_id=self.warehouse_id).exclude(pk=self.pk).count() + 1
            self.code = f"{self.warehouse.code}-C{n}"[:20]
        super().save(*args, **kwargs)

    @property
    def open_session(self):
        return self.sessions.filter(status='open').select_related('cashier').first()

    @property
    def last_session(self):
        return self.sessions.filter(status='closed').order_by('-closed_at').first()


class POSSession(models.Model):
    """Session de caisse : ouverture (fond de caisse) → ventes → fermeture (comptage, écart)."""
    STATUS_CHOICES = [
        ('open', 'Ouverte'),
        ('closed', 'Fermée'),
    ]

    session_number = models.CharField(max_length=50, unique=True, editable=False)
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='pos_sessions')
    register = models.ForeignKey(CashRegister, on_delete=models.RESTRICT, null=True, blank=True, related_name='sessions')
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cashier_sessions')
    # Entrepôt où se trouve la caisse : le stock vendu en sort
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='pos_sessions')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')

    # Fonds de caisse
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Fond de caisse d'ouverture")
    closing_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_count = models.JSONField(default=dict, blank=True, help_text='Comptage par coupure à la fermeture')

    # Totaux (recalculés par calculate_totals)
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_card = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_mobile_money = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_refunds = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_discounts = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_in = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Apports (hors ventes)')
    cash_out = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Sorties (hors remboursements)')

    sales_count = models.IntegerField(default=0)

    # Dates
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='closed_pos_sessions')

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-opened_at']
        constraints = [models.UniqueConstraint(fields=['register'], condition=Q(status='open'),
                                               name='pos_one_open_session_per_register')]

    def __str__(self):
        return f"{self.session_number} - {self.cashier.display_name}"

    def save(self, *args, **kwargs):
        if not self.session_number:
            prefix = f"SESS-{timezone.localdate():%Y%m%d}-"
            return save_with_reference(self, 'session_number', prefix, 3, lambda: super(POSSession, self).save(*args, **kwargs))
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.status == 'open'

    @property
    def net_sales(self):
        return self.total_sales - self.total_refunds

    @property
    def duration(self):
        end = self.closed_at or timezone.now()
        return end - self.opened_at

    def cash_balance(self):
        """Espèces attendues dans le tiroir = fond + entrées − sorties du journal de caisse."""
        sums = self.cash_movements.exclude(category='opening').aggregate(
            i=Sum('amount', filter=Q(type='in')), o=Sum('amount', filter=Q(type='out')))
        return self.opening_cash + (sums['i'] or 0) - (sums['o'] or 0)

    def close_session(self, closing_cash, user=None, cash_count=None):
        """Ferme la session : écart = espèces comptées − espèces attendues."""
        self.calculate_totals(save=False)
        self.status = 'closed'
        self.closed_at = timezone.now()
        self.closed_by = user or self.cashier
        self.closing_cash = Decimal(str(closing_cash))
        if cash_count is not None:
            self.cash_count = cash_count
        self.expected_cash = self.cash_balance()
        self.cash_difference = self.closing_cash - self.expected_cash
        self.save()

    def calculate_totals(self, save=True):
        """Recalcule les totaux à partir des ventes, remboursements et du journal de caisse."""
        sold = self.sales.filter(status__in=['completed', 'refunded'])
        agg = sold.aggregate(total=Sum('total_amount'), cash=Sum('cash_amount'), change=Sum('change_amount'),
                             card=Sum('card_amount'), momo=Sum('mobile_money_amount'), disc=Sum('discount_amount'))
        self.total_sales = agg['total'] or 0
        self.total_cash = (agg['cash'] or 0) - (agg['change'] or 0)
        self.total_card = agg['card'] or 0
        self.total_mobile_money = agg['momo'] or 0
        self.total_discounts = agg['disc'] or 0
        self.sales_count = sold.count()
        self.total_refunds = self.refunds.aggregate(t=Sum('amount'))['t'] or 0
        moves = self.cash_movements.exclude(category__in=['opening', 'sale', 'refund']).aggregate(
            i=Sum('amount', filter=Q(type='in')), o=Sum('amount', filter=Q(type='out')))
        self.cash_in = moves['i'] or 0
        self.cash_out = moves['o'] or 0
        if self.status == 'open':
            self.expected_cash = self.cash_balance()
        if save:
            self.save()

    def payment_breakdown(self):
        """Encaissements par moyen de paiement, remboursements déduits."""
        refunds = dict(self.refunds.values_list('method').annotate(t=Sum('amount')))
        rows = []
        for code, label, gross in (('cash', 'Espèces', self.total_cash), ('card', 'Carte bancaire', self.total_card),
                                   ('mobile_money', 'Mobile Money', self.total_mobile_money)):
            back = refunds.get(code) or 0
            rows.append({'code': code, 'label': label, 'gross': gross, 'refunds': back, 'net': gross - back})
        return rows


class POSSale(models.Model):
    """Vente en caisse"""
    STATUS_CHOICES = [
        ('pending', 'En cours'),
        ('held', 'En attente'),
        ('completed', 'Complétée'),
        ('cancelled', 'Annulée'),
        ('refunded', 'Remboursée'),
    ]

    sale_number = models.CharField(max_length=50, unique=True, editable=False)
    session = models.ForeignKey(POSSession, on_delete=models.CASCADE, related_name='sales')
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pos_sales')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='pos_sales')

    # Client (optionnel)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_purchases')
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    held_label = models.CharField(max_length=60, blank=True, help_text='Nom de la vente mise en attente')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Montants
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Paiements multiples (cash_amount = espèces reçues ; la monnaie rendue est dans change_amount)
    cash_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    card_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mobile_money_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_reference = models.CharField(max_length=100, blank=True, help_text='N° de transaction carte / Mobile Money')
    amount_tendered = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Montant reçu")
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Monnaie rendue")

    # Remboursements cumulés (TTC et TVA correspondante)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refunded_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)

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
        if self.warehouse_id is None and self.session_id:
            self.warehouse_id = self.session.warehouse_id
        if not self.sale_number:
            prefix = f"SALE-{timezone.localdate():%Y%m%d}-"
            return save_with_reference(self, 'sale_number', prefix, 5, lambda: super(POSSale, self).save(*args, **kwargs))
        super().save(*args, **kwargs)

    @property
    def net_amount(self):
        return self.total_amount - self.refunded_amount

    @property
    def refundable_amount(self):
        return max(Decimal('0'), self.total_amount - self.refunded_amount)

    @property
    def paid_amount(self):
        return self.cash_amount + self.card_amount + self.mobile_money_amount

    @property
    def main_payment_method(self):
        amounts = {'cash': self.cash_amount - self.change_amount, 'card': self.card_amount,
                   'mobile_money': self.mobile_money_amount}
        return max(amounts, key=amounts.get)

    def calculate_totals(self):
        """Calcule les totaux de la vente (remise plafonnée au sous-total)."""
        self.subtotal = sum((item.total for item in self.items.all()), Decimal('0'))

        if self.discount_percent > 0:
            self.discount_amount = (self.subtotal * self.discount_percent / 100).quantize(Decimal('1'))
        self.discount_amount = min(max(self.discount_amount, Decimal('0')), self.subtotal)

        # TVA (si applicable — la boutique n'a pas forcément de paramètres de facturation)
        settings_ = getattr(self.store, 'invoice_settings', None)
        if settings_ is not None and settings_.apply_tva:
            taxable = self.subtotal - self.discount_amount
            self.tax_amount = (taxable * (settings_.tva_rate / 100)).quantize(Decimal('1'))
        else:
            self.tax_amount = 0

        self.total_amount = self.subtotal - self.discount_amount + self.tax_amount
        self.save()

    def complete_sale(self, user=None):
        """Finalise la vente (stock, journal de caisse, totaux de session). Voir pos.services.complete_sale."""
        from . import services
        return services.finalize_sale(self, user or self.cashier)

    def refund(self, user=None, session=None, lines=None, method=None, reason=''):
        """Rembourse la vente (entièrement par défaut). Voir pos.services.refund_sale."""
        from . import services
        if self.status != 'completed':
            return False
        services.refund_sale(self, user or self.cashier, session=session, lines=lines, method=method, reason=reason)
        return True


class POSSaleItem(models.Model):
    """Ligne de vente"""
    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.SET_NULL, null=True, blank=True)

    # Détails
    product_name = models.CharField(max_length=500)
    product_sku = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    refunded_quantity = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True,
                                    help_text="Prix d'achat unitaire figé à la vente")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Options
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.product_name} x{self.quantity}"

    @property
    def refundable_quantity(self):
        return self.quantity - self.refunded_quantity

    def save(self, *args, **kwargs):
        if self.unit_cost is None and self.product_id:
            self.unit_cost = self.product.cost_price
        subtotal = Decimal(str(self.quantity)) * Decimal(str(self.unit_price))
        self.total = (subtotal - subtotal * Decimal(str(self.discount_percent)) / 100).quantize(Decimal('1'))
        super().save(*args, **kwargs)
        # Recalculer les totaux de la vente tant qu'elle n'est pas encaissée
        if self.sale.status in ('pending', 'held'):
            self.sale.calculate_totals()


class POSRefund(models.Model):
    """Remboursement (total ou partiel) d'une vente, enregistré dans la session où l'argent est rendu."""
    refund_number = models.CharField(max_length=50, unique=True, editable=False)
    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name='refunds')
    session = models.ForeignKey(POSSession, on_delete=models.CASCADE, related_name='refunds')
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='cash')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reason = models.CharField(max_length=255, blank=True)
    restock = models.BooleanField(default=True, help_text='Articles remis en stock')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.refund_number} - {self.amount} F"

    def save(self, *args, **kwargs):
        if not self.refund_number:
            prefix = f"RMB-{timezone.localdate():%Y%m%d}-"
            return save_with_reference(self, 'refund_number', prefix, 4, lambda: super(POSRefund, self).save(*args, **kwargs))
        super().save(*args, **kwargs)


class POSRefundItem(models.Model):
    refund = models.ForeignKey(POSRefund, on_delete=models.CASCADE, related_name='items')
    sale_item = models.ForeignKey(POSSaleItem, on_delete=models.CASCADE, related_name='refund_lines')
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    amount = models.DecimalField(max_digits=12, decimal_places=2)


class CashMovement(models.Model):
    """Journal de caisse : toute entrée ou sortie d'espèces du tiroir."""
    TYPE_CHOICES = [
        ('in', 'Entrée'),
        ('out', 'Sortie'),
    ]

    CATEGORY_CHOICES = [
        ('opening', 'Fond de caisse'),
        ('sale', 'Vente'),
        ('refund', 'Remboursement'),
        ('float', 'Apport de monnaie'),
        ('expense', 'Dépense'),
        ('bank_deposit', 'Dépôt banque'),
        ('withdrawal', 'Retrait'),
        ('other', 'Autre'),
    ]
    # Catégories saisies à la main (les autres sont créées par la caisse)
    MANUAL_IN = ['float', 'other']
    MANUAL_OUT = ['expense', 'bank_deposit', 'withdrawal', 'other']

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
