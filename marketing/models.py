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

    def refresh_status(self, save=True):
        """Met à jour le statut automatiquement selon les dates.
        Ne touche pas aux campagnes brouillon ou annulées."""
        now = timezone.now()
        if self.status in ('draft', 'cancelled'):
            return self.status
        if now < self.start_date:
            new_status = 'scheduled'
        elif self.start_date <= now <= self.end_date:
            new_status = 'active'
        else:
            new_status = 'completed'
        if new_status != self.status:
            self.status = new_status
            if save:
                self.save(update_fields=['status'])
        return self.status

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
    promo_code = models.ForeignKey(PromoCode, on_delete=models.SET_NULL, null=True, blank=True, related_name='newsletters')
    products = models.ManyToManyField('catalog.Product', blank=True, related_name='newsletters')
    target_audience = models.CharField(max_length=50, default='customers', help_text="all, customers, custom")
    custom_recipients = models.TextField(blank=True, help_text="Emails personnalisés, un par ligne")
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


class MessagingCampaign(models.Model):
    """Campagne WhatsApp / Telegram"""
    CHANNEL_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('telegram', 'Telegram'),
    ]
    AUDIENCE_CHOICES = [
        ('customers', 'Mes clients'),
        ('custom', 'Liste importée'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Brouillon'),
        ('in_progress', 'En cours'),
        ('done', 'Terminée'),
    ]

    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, related_name='messaging_campaigns')
    name = models.CharField(max_length=200)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default='whatsapp')
    message = models.TextField()
    promo_code = models.ForeignKey(PromoCode, on_delete=models.SET_NULL, null=True, blank=True, related_name='messaging_campaigns')
    products = models.ManyToManyField('catalog.Product', blank=True, related_name='messaging_campaigns')
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default='customers')
    custom_numbers = models.TextField(blank=True, help_text="Numéros importés, un par ligne")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sent_numbers = models.TextField(blank=True, help_text="Numéros déjà envoyés, un par ligne")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.channel})"

    def get_recipients(self):
        """Liste des numéros normalisés (format international sans +)."""
        import re
        numbers = []
        if self.audience == 'custom':
            raw = self.custom_numbers.splitlines()
        else:
            # Numéros des clients ayant commandé dans la boutique
            from orders.models import Order
            raw = Order.objects.filter(
                items__product__store=self.store
            ).values_list('shipping_phone', flat=True).distinct()
        for n in raw:
            n = re.sub(r'[^\d+]', '', str(n or '')).strip()
            if not n:
                continue
            if n.startswith('+'):
                n = n[1:]
            elif n.startswith('00237'):
                n = n[2:]
            elif len(n) == 9 and n.startswith('6'):  # numéro camerounais local
                n = '237' + n
            if len(n) >= 9 and n not in numbers:
                numbers.append(n)
        return numbers

    def get_sent_list(self):
        return [n.strip() for n in self.sent_numbers.splitlines() if n.strip()]

    @property
    def progress(self):
        total = len(self.get_recipients())
        if total == 0:
            return 0
        return round(len(self.get_sent_list()) / total * 100)

    def build_message(self, base_url='http://127.0.0.1:8000'):
        """Message final avec produits et code promo."""
        text = self.message
        products = list(self.products.all())
        if products:
            text += '\n\n🛍️ *Nos produits :*'
            for p in products:
                text += f'\n• {p.name} — {p.price:.0f} FCFA\n  {base_url}{p.get_absolute_url()}'
        if self.promo_code:
            p = self.promo_code
            reduction = f'-{p.discount_value:.0f}%' if p.discount_type == 'percentage' else f'-{p.discount_value:.0f} FCFA'
            text += f'\n\n🎁 Code promo *{p.code}* : {reduction} (jusqu\'au {p.valid_to.strftime("%d/%m/%Y")})'
        return text


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


class LoyaltySettings(models.Model):
    """Configuration du programme de fidélité, définie par le vendeur"""
    store = models.OneToOneField('store.Store', on_delete=models.CASCADE, related_name='loyalty_settings')
    points_per_amount = models.IntegerField(default=1000, help_text="1 point gagné par tranche de X FCFA")
    silver_threshold = models.IntegerField(default=2000)
    silver_rate = models.IntegerField(default=5, help_text="Réduction en %")
    gold_threshold = models.IntegerField(default=5000)
    gold_rate = models.IntegerField(default=10)
    platinum_threshold = models.IntegerField(default=10000)
    platinum_rate = models.IntegerField(default=15)

    def __str__(self):
        return f"Fidélité - {self.store.name}"

    def tier_for_points(self, points):
        if points >= self.platinum_threshold:
            return 'platinum'
        if points >= self.gold_threshold:
            return 'gold'
        if points >= self.silver_threshold:
            return 'silver'
        return 'bronze'

    def rate_for_tier(self, tier):
        return {
            'bronze': 0,
            'silver': self.silver_rate,
            'gold': self.gold_rate,
            'platinum': self.platinum_rate,
        }.get(tier, 0)


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

    def get_settings(self):
        """Configuration de la boutique (avec valeurs par défaut)."""
        settings_obj, _ = LoyaltySettings.objects.get_or_create(store=self.store)
        return settings_obj

    def add_points(self, amount):
        """Ajoute des points selon le barème de la boutique"""
        cfg = self.get_settings()
        points_to_add = int(amount / cfg.points_per_amount) if cfg.points_per_amount > 0 else 0
        self.points += points_to_add
        self.update_tier()
        self.save()
        return points_to_add

    def update_tier(self):
        """Met à jour le niveau selon les réglages de la boutique.
        Envoie un email au client si le niveau change."""
        cfg = self.get_settings()
        new_tier = cfg.tier_for_points(self.points)
        if new_tier != self.tier:
            old_tier = self.tier
            self.tier = new_tier
            self._notify_tier_change(old_tier, new_tier, cfg)

    def _notify_tier_change(self, old_tier, new_tier, cfg):
        """Email au client lors d'un changement de niveau."""
        if not self.user.email:
            return
        from django.core.mail import EmailMessage
        from django.conf import settings as dj_settings
        labels = {'bronze': 'Bronze', 'silver': 'Argent', 'gold': 'Or', 'platinum': 'Platine'}
        rate = cfg.rate_for_tier(new_tier)
        benefit = f"Vous bénéficiez désormais de <strong>-{rate}%</strong> automatiques sur toutes vos commandes chez {self.store.name}." if rate > 0 else "Continuez vos achats pour débloquer des réductions automatiques."
        html = f'''<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#fff;">
    <div style="background:#ff6a00;padding:24px;text-align:center;">
        <div style="color:#fff;font-size:22px;font-weight:800;">{self.store.name}</div>
    </div>
    <div style="padding:32px 28px;color:#333;font-size:14px;line-height:1.7;">
        <p>Bonjour {self.user.first_name or self.user.username},</p>
        <p>Félicitations ! Vous passez au niveau <strong style="color:#ff6a00;">{labels.get(new_tier, new_tier)}</strong> de notre programme de fidélité ({self.points} points).</p>
        <p>{benefit}</p>
        <p>Merci de votre confiance.</p>
    </div>
    <div style="background:#f9fafb;padding:18px;text-align:center;font-size:11px;color:#98a2b3;">
        {self.store.name} · {self.store.city or 'Douala'} · AfriMarket
    </div>
</div>
</body></html>'''
        try:
            msg = EmailMessage(
                subject=f'Vous êtes maintenant niveau {labels.get(new_tier, new_tier)} chez {self.store.name} !',
                body=html,
                from_email=dj_settings.DEFAULT_FROM_EMAIL,
                to=[self.user.email],
            )
            msg.content_subtype = 'html'
            msg.send(fail_silently=True)
        except Exception:
            pass

    def redeem_points(self, points):
        """Utilise des points (100 points = 1000 FCFA)"""
        if points > self.points:
            return False
        self.points -= points
        self.save()
        return True

    @property
    def discount_rate(self):
        """Taux de réduction selon le niveau et les réglages de la boutique"""
        return self.get_settings().rate_for_tier(self.tier)
