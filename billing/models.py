from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class SubscriptionPlan(models.Model):
    """Packages d'abonnement pour les vendeurs"""
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=0)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True)
    max_products = models.IntegerField(default=10)
    max_images_per_product = models.IntegerField(default=3)
    commission_rate = models.DecimalField(max_digits=4, decimal_places=2, default=10.00, help_text='% commission on sales')
    can_flash_deal = models.BooleanField(default=False)
    can_boost = models.BooleanField(default=False)
    can_analytics = models.BooleanField(default=False)
    can_bulk_upload = models.BooleanField(default=False)
    can_priority_support = models.BooleanField(default=False)
    can_custom_store = models.BooleanField(default=False)
    boost_credits_monthly = models.IntegerField(default=0)
    featured_products = models.IntegerField(default=0, help_text='Number of featured product slots')
    badge_level = models.CharField(max_length=20, choices=[
        ('basic', 'Basic'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum'),
    ], default='basic')
    is_active = models.BooleanField(default=True)
    is_popular = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    features = models.TextField(blank=True, help_text='One feature per line')

    class Meta:
        ordering = ['order', 'price_monthly']

    def __str__(self):
        return f"{self.name} ({self.price_monthly} F/mois)"

    @property
    def feature_list(self):
        return [f.strip() for f in self.features.split('\n') if f.strip()]

    @property
    def yearly_savings(self):
        if self.price_yearly:
            return int(self.price_monthly * 12 - self.price_yearly)
        return 0


class Subscription(models.Model):
    """Abonnement actif d'un vendeur"""
    STATUS_CHOICES = [
        ('active', 'Actif'),
        ('expired', 'Expiré'),
        ('cancelled', 'Annulé'),
        ('trial', 'Essai gratuit'),
    ]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='trial')
    billing_cycle = models.CharField(max_length=10, choices=[('monthly', 'Mensuel'), ('yearly', 'Annuel')], default='monthly')
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField()
    auto_renew = models.BooleanField(default=True)
    boost_credits_remaining = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    @property
    def is_active(self):
        return self.status in ['active', 'trial'] and self.end_date > timezone.now()

    @property
    def days_remaining(self):
        delta = self.end_date - timezone.now()
        return max(0, delta.days)

    @property
    def current_amount(self):
        if self.billing_cycle == 'yearly' and self.plan.price_yearly:
            return self.plan.price_yearly
        return self.plan.price_monthly


class SubscriptionPayment(models.Model):
    """Historique des paiements d'abonnement"""
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=0)
    payment_method = models.CharField(max_length=20, choices=[
        ('momo', 'MTN MoMo'), ('om', 'Orange Money'), ('card', 'Carte bancaire'), ('transfer', 'Virement'),
    ])
    transaction_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', 'En attente'), ('completed', 'Complété'), ('failed', 'Échoué'),
    ], default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.subscription.user.username} - {self.amount}F - {self.status}"


class PaymentConfig(models.Model):
    """Configuration des moyens de paiement pour un vendeur"""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payment_config')
    # MoMo
    momo_enabled = models.BooleanField(default=False)
    momo_number = models.CharField(max_length=20, blank=True)
    momo_name = models.CharField(max_length=100, blank=True)
    # Orange Money
    om_enabled = models.BooleanField(default=False)
    om_number = models.CharField(max_length=20, blank=True)
    om_name = models.CharField(max_length=100, blank=True)
    # Bank
    bank_enabled = models.BooleanField(default=False)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    bank_account_name = models.CharField(max_length=100, blank=True)
    # Cash
    cash_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"Config paiement: {self.user.username}"

    @property
    def enabled_methods(self):
        methods = []
        if self.momo_enabled: methods.append(('momo', 'MTN MoMo', self.momo_number))
        if self.om_enabled: methods.append(('om', 'Orange Money', self.om_number))
        if self.bank_enabled: methods.append(('bank', 'Virement', self.bank_name))
        if self.cash_enabled: methods.append(('cash', 'Cash', ''))
        return methods


class ProductBoost(models.Model):
    """Système de boost/promotion de produits"""
    PLATFORM_CHOICES = [
        ('internal', 'AfriMarket (page d\'accueil)'),
        ('facebook', 'Facebook'),
        ('whatsapp', 'WhatsApp Status'),
    ]
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('active', 'Actif'),
        ('completed', 'Terminé'),
        ('cancelled', 'Annulé'),
        ('failed', 'Échoué'),
    ]
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE, related_name='boosts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    budget = models.DecimalField(max_digits=10, decimal_places=0, default=0)

    # Financial tracking
    client_budget = models.DecimalField(max_digits=10, decimal_places=0, default=0, help_text='Montant payé par le client')
    platform_budget = models.DecimalField(max_digits=10, decimal_places=0, default=0, help_text='Montant envoyé à Facebook/WhatsApp')
    commission = models.DecimalField(max_digits=10, decimal_places=0, default=0, help_text='Commission AfriMarket')

    # External campaign tracking
    external_campaign_id = models.CharField(max_length=200, blank=True, help_text='ID de campagne Facebook/WhatsApp')
    targeting_data = models.JSONField(null=True, blank=True, help_text='Données de ciblage (ville, âge, etc.)')
    error_message = models.TextField(blank=True, help_text='Message d\'erreur en cas d\'échec')

    duration_days = models.IntegerField(default=7)
    impressions = models.IntegerField(default=0)
    clicks = models.IntegerField(default=0)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Boost: {self.product.name[:30]} on {self.platform}"

    @property
    def ctr(self):
        if self.impressions > 0:
            return round((self.clicks / self.impressions) * 100, 1)
        return 0

    @property
    def cost_per_click(self):
        if self.clicks > 0:
            return round(int(self.budget) / self.clicks)
        return 0
