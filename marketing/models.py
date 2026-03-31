from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal


class PromoCode(models.Model):
    """Code promo pour réductions"""
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Pourcentage'),
        ('fixed', 'Montant fixe'),
    ]

    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='promo_codes')
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='percentage')
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text="Pourcentage ou montant fixe")
    min_purchase_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Montant minimum d'achat")
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Montant maximum de réduction")
    usage_limit = models.IntegerField(null=True, blank=True, help_text="Nombre d'utilisations maximum")
    usage_count = models.IntegerField(default=0)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_to = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} - {self.store.name}"

    @property
    def is_valid(self):
        now = timezone.now()
        if not self.is_active:
            return False
        if self.valid_from > now or self.valid_to < now:
            return False
        if self.usage_limit and self.usage_count >= self.usage_limit:
            return False
        return True

    def calculate_discount(self, amount):
        """Calcule le montant de la réduction"""
        if not self.is_valid:
            return 0
        if amount < self.min_purchase_amount:
            return 0

        if self.discount_type == 'percentage':
            discount = amount * (self.discount_value / 100)
        else:
            discount = self.discount_value

        if self.max_discount_amount:
            discount = min(discount, self.max_discount_amount)

        return discount

    def apply_code(self):
        """Incrémente le compteur d'utilisation"""
        self.usage_count += 1
        self.save()


class Campaign(models.Model):
    """Campagne marketing"""
    CAMPAIGN_TYPE_CHOICES = [
        ('flash_sale', 'Vente Flash'),
        ('seasonal', 'Promotion Saisonnière'),
        ('newsletter', 'Newsletter'),
        ('product_launch', 'Lancement Produit'),
        ('clearance', 'Déstockage'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Brouillon'),
        ('scheduled', 'Programmée'),
        ('active', 'Active'),
        ('completed', 'Terminée'),
        ('cancelled', 'Annulée'),
    ]

    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=200)
    campaign_type = models.CharField(max_length=50, choices=CAMPAIGN_TYPE_CHOICES)
    description = models.TextField()
    banner = models.ImageField(upload_to='campaigns/', blank=True, null=True)
    target_products = models.ManyToManyField('catalog.Product', blank=True, related_name='campaigns')
    promo_code = models.ForeignKey(PromoCode, on_delete=models.SET_NULL, null=True, blank=True, related_name='campaigns')
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    views_count = models.IntegerField(default=0)
    clicks_count = models.IntegerField(default=0)
    conversions_count = models.IntegerField(default=0)
    revenue_generated = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.store.name}"

    @property
    def is_active(self):
        now = timezone.now()
        return self.status == 'active' and self.start_date <= now <= self.end_date

    @property
    def conversion_rate(self):
        if self.clicks_count == 0:
            return 0
        return round((self.conversions_count / self.clicks_count) * 100, 2)

    @property
    def roi(self):
        if self.budget == 0:
            return 0
        return round(((self.revenue_generated - self.budget) / self.budget) * 100, 2)


class Newsletter(models.Model):
    """Newsletter pour marketing par email"""
    STATUS_CHOICES = [
        ('draft', 'Brouillon'),
        ('scheduled', 'Programmée'),
        ('sent', 'Envoyée'),
    ]

    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='newsletters')
    subject = models.CharField(max_length=200)
    content = models.TextField(help_text="Contenu HTML de la newsletter")
    campaign = models.ForeignKey(Campaign, on_delete=models.SET_NULL, null=True, blank=True, related_name='newsletters')
    target_audience = models.CharField(max_length=50, default='all', help_text="all, buyers, vip")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    recipients_count = models.IntegerField(default=0)
    opened_count = models.IntegerField(default=0)
    clicked_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subject} - {self.store.name}"

    @property
    def open_rate(self):
        if self.recipients_count == 0:
            return 0
        return round((self.opened_count / self.recipients_count) * 100, 2)

    @property
    def click_rate(self):
        if self.recipients_count == 0:
            return 0
        return round((self.clicked_count / self.recipients_count) * 100, 2)


class MarketingAnalytics(models.Model):
    """Analytics marketing quotidiens"""
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='analytics')
    date = models.DateField()
    page_views = models.IntegerField(default=0)
    unique_visitors = models.IntegerField(default=0)
    product_views = models.IntegerField(default=0)
    add_to_cart = models.IntegerField(default=0)
    purchases = models.IntegerField(default=0)
    revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    avg_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bounce_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        ordering = ['-date']
        unique_together = ['store', 'date']

    def __str__(self):
        return f"{self.store.name} - {self.date}"

    @property
    def conversion_rate(self):
        if self.unique_visitors == 0:
            return 0
        return round((self.purchases / self.unique_visitors) * 100, 2)


class LoyaltyProgram(models.Model):
    """Programme de fidélité"""
    TIER_CHOICES = [
        ('bronze', 'Bronze'),
        ('silver', 'Argent'),
        ('gold', 'Or'),
        ('platinum', 'Platine'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='loyalty_programs')
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='loyalty_members')
    points = models.IntegerField(default=0)
    tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='bronze')
    total_spent = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_orders = models.IntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'store']

    def __str__(self):
        return f"{self.user.username} - {self.store.name} ({self.tier})"

    def add_points(self, amount):
        """Ajoute des points (1 point par 1000 FCFA dépensés)"""
        points_to_add = int(amount / 1000)
        self.points += points_to_add
        self.update_tier()
        self.save()
        return points_to_add

    def update_tier(self):
        """Met à jour le niveau selon les points"""
        if self.points >= 10000:
            self.tier = 'platinum'
        elif self.points >= 5000:
            self.tier = 'gold'
        elif self.points >= 2000:
            self.tier = 'silver'
        else:
            self.tier = 'bronze'

    def redeem_points(self, points):
        """Utilise des points (100 points = 1000 FCFA)"""
        if points > self.points:
            return False
        self.points -= points
        self.save()
        return True

    @property
    def discount_rate(self):
        """Taux de réduction selon le niveau"""
        rates = {
            'bronze': 0,
            'silver': 5,
            'gold': 10,
            'platinum': 15,
        }
        return rates.get(self.tier, 0)
