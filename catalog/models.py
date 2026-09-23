from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.urls import reverse


class Category(models.Model):
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    icon = models.CharField(max_length=50, default='📦')
    image = models.ImageField(upload_to='categories/', blank=True, null=True)
    description = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def is_parent(self):
        return self.parent is None

    def get_absolute_url(self):
        return reverse('catalog:category', kwargs={'slug': self.slug})


class Product(models.Model):
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=500)
    slug = models.SlugField(max_length=500, unique=True)
    description = models.TextField()
    specifications = models.TextField(blank=True, help_text='One per line: Key: Value')
    price = models.DecimalField(max_digits=12, decimal_places=0)
    old_price = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    min_order = models.IntegerField(default=1, help_text='Minimum order quantity')
    bulk_price = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    bulk_min_qty = models.IntegerField(default=10)
    stock = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5, help_text='Seuil d\'alerte stock faible')
    sku = models.CharField(max_length=50, blank=True)
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    image_2 = models.ImageField(upload_to='products/', blank=True, null=True)
    image_3 = models.ImageField(upload_to='products/', blank=True, null=True)
    image_4 = models.ImageField(upload_to='products/', blank=True, null=True)
    colors = models.CharField(max_length=300, blank=True, help_text='Comma separated')
    sizes = models.CharField(max_length=300, blank=True, help_text='Comma separated')
    weight = models.CharField(max_length=50, blank=True)
    origin = models.CharField(max_length=100, default='Cameroun')
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    is_flash_deal = models.BooleanField(default=False)
    flash_deal_end = models.DateTimeField(null=True, blank=True)
    views_count = models.IntegerField(default=0)
    orders_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:80]
            self.slug = base
            counter = 1
            while Product.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('catalog:product_detail', kwargs={'slug': self.slug})

    @property
    def discount_percent(self):
        if self.old_price and self.old_price > self.price:
            return int(((self.old_price - self.price) / self.old_price) * 100)
        return 0

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def is_low_stock(self):
        return 0 < self.stock <= self.low_stock_threshold

    @property
    def stock_status(self):
        if self.stock == 0:
            return 'out'
        if self.is_low_stock:
            return 'low'
        return 'ok'

    def adjust_stock(self, quantity, movement_type, user=None, reason='', reference='', warehouse=None, update_global=True):
        """Ajuste le stock et enregistre le mouvement. Synchronise l'entrepôt concerné.
        update_global=False pour les transferts (le stock global ne change pas)."""
        before = self.stock
        if update_global:
            self.stock = max(0, self.stock + quantity)
            self.save(update_fields=['stock'])
        # Synchroniser le stock de l'entrepôt (par défaut si non précisé)
        try:
            from inventory.models import Warehouse, ProductStock
            wh = warehouse or Warehouse.objects.filter(store=self.store, is_default=True).first()
            if wh:
                ps, _ = ProductStock.objects.get_or_create(product=self, warehouse=wh)
                ps.quantity = max(0, ps.quantity + quantity)
                ps.save(update_fields=['quantity'])
        except Exception:
            pass
        # Alerte stock faible : notifier le vendeur quand on passe sous le seuil
        if update_global and quantity < 0 and self.low_stock_threshold:
            if before > self.low_stock_threshold and self.stock <= self.low_stock_threshold:
                try:
                    from messaging.utils import notify
                    notify(
                        self.store.owner, 'stock',
                        f'Stock faible : {self.name}',
                        f'Il ne reste que {self.stock} unité(s) de « {self.name} » (seuil : {self.low_stock_threshold}). Pensez à réapprovisionner.',
                        url='/dashboard/produits/',
                    )
                except Exception:
                    pass
        return StockMovement.objects.create(
            product=self, store=self.store, movement_type=movement_type,
            quantity=quantity, stock_before=before, stock_after=self.stock,
            reason=reason, reference=reference, created_by=user,
            warehouse=warehouse,
        )

    @property
    def avg_rating(self):
        avg = self.reviews.aggregate(avg=models.Avg('rating'))['avg']
        return round(avg, 1) if avg else 0

    @property
    def review_count(self):
        return self.reviews.count()

    @property
    def spec_list(self):
        if not self.specifications:
            return []
        specs = []
        for line in self.specifications.strip().split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                specs.append((k.strip(), v.strip()))
        return specs

    @property
    def color_list(self):
        return [c.strip() for c in self.colors.split(',') if c.strip()] if self.colors else []

    @property
    def size_list(self):
        return [s.strip() for s in self.sizes.split(',') if s.strip()] if self.sizes else []

    @property
    def all_images(self):
        imgs = []
        for f in [self.image, self.image_2, self.image_3, self.image_4]:
            if f:
                imgs.append(f.url)
        return imgs


class StockMovement(models.Model):
    """Traçabilité complète des mouvements de stock (style Odoo)"""
    TYPE_CHOICES = [
        ('in', 'Entrée'),
        ('out', 'Sortie'),
        ('adjustment', 'Ajustement'),
        ('sale', 'Vente en ligne'),
        ('pos', 'Vente POS'),
        ('return', 'Retour / Annulation'),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_movements')
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='stock_movements')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantity = models.IntegerField(help_text='Positif = entrée, négatif = sortie')
    stock_before = models.PositiveIntegerField()
    stock_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text='N° commande, vente POS...')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity:+d} — {self.product.name}"


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    rating = models.IntegerField(choices=[(i, i) for i in range(1, 6)])
    comment = models.TextField()
    image = models.ImageField(upload_to='reviews/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.rating}★ - {self.product.name[:30]}"


class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlists')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')


class HeroBanner(models.Model):
    title = models.CharField(max_length=200)
    subtitle = models.CharField(max_length=500, blank=True)
    image = models.ImageField(upload_to='banners/')
    button_text = models.CharField(max_length=100, blank=True)
    button_url = models.CharField(max_length=500, blank=True)
    button2_text = models.CharField(max_length=100, blank=True)
    button2_url = models.CharField(max_length=500, blank=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title


class FlashDeal(models.Model):
    title = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    @property
    def products(self):
        return Product.objects.filter(is_flash_deal=True, flash_deal_end__gte=self.start_time)


class ContactMessage(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.subject}"
