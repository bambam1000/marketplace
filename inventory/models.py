from django.db import models
from django.conf import settings
from django.utils import timezone


class Warehouse(models.Model):
    """Entrepôt / point de stockage — peut servir plusieurs boutiques"""
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='warehouses')
    linked_stores = models.ManyToManyField('store.Store', blank=True, related_name='linked_warehouses',
                                           help_text='Boutiques supplémentaires alimentées par cet entrepôt')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, help_text='Ex: DLA-01')
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, default='Douala')
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_warehouses')
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('store', 'code')
        ordering = ['-is_default', 'name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def total_products(self):
        return self.stocks.filter(quantity__gt=0).count()

    @property
    def stock_value(self):
        return sum(int(s.product.price) * s.quantity for s in self.stocks.select_related('product'))

    @property
    def out_of_stock_count(self):
        return self.stocks.filter(quantity=0).count()


class ProductStock(models.Model):
    """Stock d'un produit dans un entrepôt précis"""
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE, related_name='warehouse_stocks')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stocks')
    quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)

    class Meta:
        unique_together = ('product', 'warehouse')

    def __str__(self):
        return f"{self.product.name} @ {self.warehouse.code}: {self.quantity}"


class StockTransfer(models.Model):
    """Transfert de stock entre deux entrepôts"""
    STATUS_CHOICES = [
        ('draft', 'Brouillon'),
        ('confirmed', 'Confirmé'),
        ('in_transit', 'En transit'),
        ('received', 'Reçu'),
        ('cancelled', 'Annulé'),
    ]
    TYPE_CHOICES = [
        ('transfer', 'Transfert'),
        ('return', 'Retour'),
    ]
    reference = models.CharField(max_length=20, unique=True, editable=False)
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='transfers')
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='transfers_out')
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='transfers_in')
    transfer_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='transfer')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        if not self.reference:
            count = StockTransfer.objects.count() + 1
            prefix = 'RET' if self.transfer_type == 'return' else 'TRF'
            self.reference = f"{prefix}-{count:05d}"
        super().save(*args, **kwargs)

    def confirm(self):
        """Confirme : sort le stock de l'entrepôt source."""
        if self.status != 'draft':
            return False
        for item in self.items.all():
            item.product.adjust_stock(-item.quantity, 'out', user=self.created_by,
                                      reason=f'Transfert vers {self.to_warehouse.name}',
                                      reference=self.reference, warehouse=self.from_warehouse,
                                      update_global=False)
        self.status = 'in_transit'
        self.confirmed_at = timezone.now()
        self.save()
        return True

    def receive(self):
        """Réception : entre le stock dans l'entrepôt destination."""
        if self.status != 'in_transit':
            return False
        for item in self.items.all():
            qty = item.quantity_received or item.quantity
            item.product.adjust_stock(qty, 'in', user=self.created_by,
                                      reason=f'Réception depuis {self.from_warehouse.name}',
                                      reference=self.reference, warehouse=self.to_warehouse,
                                      update_global=False)
        self.status = 'received'
        self.received_at = timezone.now()
        self.save()
        return True

    def cancel(self):
        if self.status in ['received', 'cancelled']:
            return False
        # Si déjà confirmé, remettre le stock dans la source
        if self.status == 'in_transit':
            for item in self.items.all():
                item.product.adjust_stock(item.quantity, 'return', user=self.created_by,
                                          reason='Annulation transfert',
                                          reference=self.reference, warehouse=self.from_warehouse,
                                          update_global=False)
        self.status = 'cancelled'
        self.save()
        return True


class TransferItem(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    quantity_received = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"
