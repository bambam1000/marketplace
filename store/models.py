from django.db import models
from django.conf import settings
from django.utils.text import slugify


class Store(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='store')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='stores/logos/', blank=True, null=True)
    banner = models.ImageField(upload_to='stores/banners/', blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20, blank=True, help_text="Numéro WhatsApp avec indicatif pays (ex: 237677123456)")
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, default='Douala')
    country = models.CharField(max_length=100, default='Cameroun')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, help_text="Latitude GPS")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, help_text="Longitude GPS")
    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    response_rate = models.IntegerField(default=95)
    response_time = models.CharField(max_length=50, default='< 24h')
    year_established = models.IntegerField(default=2024)
    total_revenue = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def product_count(self):
        return self.products.filter(is_active=True).count()

    @property
    def rating(self):
        from catalog.models import Review
        avg = Review.objects.filter(product__store=self).aggregate(
            avg=models.Avg('rating'))['avg']
        return round(avg, 1) if avg else 4.5

    @property
    def total_sales(self):
        from orders.models import OrderItem
        return OrderItem.objects.filter(
            product__store=self, order__status='delivered'
        ).aggregate(total=models.Sum('quantity'))['total'] or 0
