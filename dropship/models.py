"""Dropshipping : un fournisseur est un entrepôt de la boutique.

Les produits du fournisseur ont leur stock dans cet entrepôt (copie du stock fournisseur) ;
les lignes de commande rattachées à cet entrepôt deviennent des commandes fournisseur.
"""
import math
from decimal import Decimal

from django.conf import settings
from django.db import models

from config.numbering import save_with_reference


class SupplierConnection(models.Model):
    PROVIDER_CHOICES = [
        ('file', 'Fichier CSV / Excel'),
        ('cj', 'CJ Dropshipping'),
        ('aliexpress', 'AliExpress'),
    ]
    CURRENCY_CHOICES = [
        ('XAF', 'Franc CFA (F)'),
        ('USD', 'Dollar américain ($)'),
        ('EUR', 'Euro (€)'),
        ('CNY', 'Yuan chinois (¥)'),
    ]
    warehouse = models.OneToOneField('inventory.Warehouse', on_delete=models.CASCADE, related_name='supplier')
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default='file')
    name = models.CharField(max_length=150, help_text='Nom du fournisseur')
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=100, default='Chine', help_text="Pays d'expédition")
    notes = models.TextField(blank=True)
    # Prix : coût de revient = prix fournisseur × taux + frais par article ; prix de vente = coût de revient × coefficient
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='USD')
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('610'),
                                        help_text='Francs CFA pour 1 unité de la devise du fournisseur')
    fixed_fee = models.DecimalField(max_digits=12, decimal_places=0, default=0,
                                    help_text='Frais par article (transport, douane), en francs')
    markup = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1.80'),
                                 help_text='Coefficient appliqué au coût de revient')
    round_to = models.PositiveIntegerField(default=100, help_text='Arrondi du prix de vente (au-dessus)')
    min_margin_pct = models.PositiveSmallIntegerField(default=15, help_text='Alerte si la marge passe sous ce seuil')
    delivery_min_days = models.PositiveSmallIntegerField(default=10)
    delivery_max_days = models.PositiveSmallIntegerField(default=25)
    is_active = models.BooleanField(default=True)
    last_import_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Connexion directe (API CJ Dropshipping) : clé et jetons chiffrés (voir secret.py)
    api_key_enc = models.TextField(blank=True)
    api_key_hint = models.CharField(max_length=8, blank=True, help_text='4 derniers caractères, pour affichage')
    access_token_enc = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_token_enc = models.TextField(blank=True)
    refresh_expires_at = models.DateTimeField(null=True, blank=True)
    api_connected_at = models.DateTimeField(null=True, blank=True)
    api_error = models.CharField(max_length=300, blank=True)
    logistic_name = models.CharField(max_length=100, default='CJPacket Ordinary', blank=True,
                                     help_text='Mode de livraison demandé au fournisseur')
    ship_to_country = models.CharField(max_length=2, default='CM', help_text='Pays de livraison des clients (code ISO)')
    include_shipping = models.BooleanField(default=True, help_text='Ajouter le port du fournisseur au coût de revient')
    auto_send = models.BooleanField(default=False, help_text='Transmettre automatiquement les commandes payées')
    pay_from_balance = models.BooleanField(default=False, help_text='Payer le fournisseur avec le solde du compte')
    last_sync_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.name} ({self.get_provider_display()})'

    @property
    def api_ready(self):
        """Connexion directe utilisable (CJ avec une clé enregistrée)."""
        return self.provider == 'cj' and bool(self.api_key_enc)

    @property
    def rate(self):
        return Decimal(1) if self.currency == 'XAF' else self.exchange_rate

    def landed_cost(self, cost):
        """Coût de revient d'un article, en francs (arrondi au franc supérieur)."""
        return int(math.ceil(Decimal(cost) * self.rate + self.fixed_fee))

    def sale_price(self, cost):
        step = self.round_to or 1
        return int(math.ceil(Decimal(self.landed_cost(cost)) * self.markup / step) * step)

    @staticmethod
    def margin_pct(price, landed):
        price = Decimal(price or 0)
        return int((price - Decimal(landed)) / price * 100) if price > 0 else 0


class SupplierProduct(models.Model):
    """Produit du catalogue Comptoir fourni par un fournisseur."""
    connection = models.ForeignKey(SupplierConnection, on_delete=models.CASCADE, related_name='items')
    product = models.OneToOneField('catalog.Product', on_delete=models.CASCADE, related_name='supplier_item')
    supplier_sku = models.CharField(max_length=120)
    supplier_url = models.URLField(max_length=1000, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, help_text='Prix fournisseur, dans sa devise')
    supplier_stock = models.PositiveIntegerField(default=0)
    auto_price = models.BooleanField(default=True, help_text='Recalculer le prix de vente quand le coût change')
    image_urls = models.JSONField(default=list, blank=True, help_text='Images encore à télécharger')
    supplier_pid = models.CharField(max_length=100, blank=True, help_text='Identifiant du produit chez le fournisseur (API)')
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                        help_text='Port fournisseur par article, dans sa devise')
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('connection', 'supplier_sku')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.supplier_sku} → {self.product_id}'

    @property
    def total_cost(self):
        """Prix fournisseur + port, dans la devise du fournisseur."""
        return self.cost + (self.shipping_cost or 0)

    def variant_for(self, color='', size=''):
        """Variante fournisseur correspondant au choix du client (couleur / taille)."""
        variants = list(self.variants.all())
        if not variants:
            return None
        norm = lambda t: (t or '').strip().lower()  # noqa: E731
        color, size = norm(color), norm(size)
        for v in variants:
            if norm(v.color) == color and norm(v.size) == size:
                return v
        if color or size:
            for v in variants:
                if (not color or norm(v.color) == color) and (not size or norm(v.size) == size):
                    return v
        return variants[0] if len(variants) == 1 or not (color or size) else None


class SupplierVariant(models.Model):
    """Variante d'un produit fournisseur (couleur / taille) : c'est elle qu'on commande par l'API."""
    item = models.ForeignKey(SupplierProduct, on_delete=models.CASCADE, related_name='variants')
    vid = models.CharField(max_length=100)
    sku = models.CharField(max_length=120, blank=True)
    label = models.CharField(max_length=200, blank=True)
    color = models.CharField(max_length=100, blank=True)
    size = models.CharField(max_length=100, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('item', 'vid')
        ordering = ['pk']

    def __str__(self):
        return self.label or self.vid


class SupplierImport(models.Model):
    STATUS_CHOICES = [
        ('mapping', 'Colonnes à associer'),
        ('done', 'Terminé'),
        ('failed', 'Échec'),
    ]
    connection = models.ForeignKey(SupplierConnection, on_delete=models.CASCADE, related_name='imports')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    filename = models.CharField(max_length=255)
    headers = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    mapping = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='mapping')
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=list, blank=True, help_text='[[ligne, message], …]')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class SupplierOrder(models.Model):
    """Part d'une commande client à faire expédier par le fournisseur."""
    STATUS_CHOICES = [
        ('to_send', 'À transmettre'),
        ('sent', 'Transmise'),
        ('shipped', 'Expédiée'),
        ('delivered', 'Livrée'),
        ('to_cancel', 'À annuler chez le fournisseur'),
        ('cancelled', 'Annulée'),
    ]
    OPEN = ('to_send', 'sent', 'shipped', 'to_cancel')
    reference = models.CharField(max_length=20, unique=True, editable=False)
    connection = models.ForeignKey(SupplierConnection, on_delete=models.CASCADE, related_name='orders')
    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='supplier_orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='to_send')
    cost = models.DecimalField(max_digits=14, decimal_places=0, default=0, help_text='Coût de revient total, en francs')
    supplier_reference = models.CharField(max_length=100, blank=True, help_text='Numéro de commande chez le fournisseur')
    carrier = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    tracking_url = models.URLField(max_length=500, blank=True)
    supplier_status = models.CharField(max_length=30, blank=True, help_text="Statut renvoyé par l'API du fournisseur")
    supplier_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                          help_text='Montant facturé par le fournisseur, dans sa devise')
    last_error = models.CharField(max_length=300, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('connection', 'order')
        ordering = ['-created_at']

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        if not self.reference:
            return save_with_reference(self, 'reference', 'DRP-', 5, lambda: super(SupplierOrder, self).save(*args, **kwargs))
        super().save(*args, **kwargs)

    @property
    def lines(self):
        return self.order.items.filter(warehouse=self.connection.warehouse).select_related('product')

    @property
    def can_send(self):
        """On ne paie le fournisseur que pour une commande payée par le client."""
        return self.status == 'to_send' and self.order.is_paid and self.order.status not in ('cancelled', 'refunded')
