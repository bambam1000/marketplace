from django.db import models
from django.conf import settings
import uuid


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('confirmed', 'Confirmée'),
        ('processing', 'En traitement'),
        ('shipped', 'Expédiée'),
        ('delivered', 'Livrée'),
        ('cancelled', 'Annulée'),
        ('refunded', 'Remboursée'),
    ]
    PAYMENT_CHOICES = [
        ('momo', 'MTN Mobile Money'),
        ('om', 'Orange Money'),
        ('cash', 'Cash à la livraison'),
        ('card', 'Carte bancaire'),
        ('transfer', 'Virement bancaire'),
    ]

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='momo')
    subtotal = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    promo_code = models.ForeignKey('marketing.PromoCode', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    discount_amount = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    loyalty_discount = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_amount = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    shipping_name = models.CharField(max_length=200)
    shipping_phone = models.CharField(max_length=20)
    shipping_address = models.TextField()
    shipping_city = models.CharField(max_length=100)
    notes = models.TextField(blank=True)
    is_paid = models.BooleanField(default=False)
    tracking_number = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.order_number}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = f"ALB-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    @property
    def status_color(self):
        colors = {
            'pending': 'orange', 'confirmed': 'blue', 'processing': 'purple',
            'shipped': 'teal', 'delivered': 'green', 'cancelled': 'red', 'refunded': 'gray',
        }
        return colors.get(self.status, 'gray')

    @property
    def progress_percent(self):
        steps = {'pending': 20, 'confirmed': 40, 'processing': 60, 'shipped': 80, 'delivered': 100, 'cancelled': 0}
        return steps.get(self.status, 0)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE)
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE)
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=0)
    color = models.CharField(max_length=50, blank=True)
    size = models.CharField(max_length=50, blank=True)

    @property
    def subtotal(self):
        return self.price * self.quantity


class RFQ(models.Model):
    """Request for Quotation - Alibaba key feature"""
    STATUS_CHOICES = [
        ('open', 'Ouverte'),
        ('quoted', 'Devis reçu'),
        ('accepted', 'Acceptée'),
        ('closed', 'Fermée'),
    ]
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rfqs')
    product_name = models.CharField(max_length=500)
    category = models.ForeignKey('catalog.Category', on_delete=models.SET_NULL, null=True)
    description = models.TextField()
    quantity = models.IntegerField()
    target_price = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    unit = models.CharField(max_length=50, default='pièce')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"RFQ: {self.product_name}"

    @property
    def quote_count(self):
        return self.quotes.count()


class Quote(models.Model):
    """Devis proposé par un vendeur en réponse à une RFQ"""
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('accepted', 'Accepté'),
        ('rejected', 'Rejeté'),
    ]
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name='quotes')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quotes')
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='quotes')
    price_per_unit = models.DecimalField(max_digits=12, decimal_places=0, help_text="Prix par unité en FCFA")
    total_price = models.DecimalField(max_digits=15, decimal_places=0, help_text="Prix total en FCFA")
    delivery_time = models.CharField(max_length=100, help_text="Ex: 7-10 jours")
    payment_terms = models.CharField(max_length=200, default="50% à la commande, 50% à la livraison")
    description = models.TextField(help_text="Détails de votre offre")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['rfq', 'seller']  # Un vendeur ne peut soumettre qu'un seul devis par RFQ

    def __str__(self):
        return f"Devis de {self.store.name} pour {self.rfq.product_name}"

    def save(self, *args, **kwargs):
        # Calculer le prix total automatiquement
        if not self.total_price:
            self.total_price = self.price_per_unit * self.rfq.quantity
        super().save(*args, **kwargs)
