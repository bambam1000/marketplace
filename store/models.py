from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify


class Store(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='stores')
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
    opening_hours = models.CharField(max_length=200, blank=True, help_text='Ex: Lun-Sam 8h-18h')
    facebook = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
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

# ======== RÔLES & EMPLOYÉS (gestion des droits par boutique) ========

class StoreRole(models.Model):
    """Rôle personnalisé avec permissions granulaires (style Odoo)"""
    PERMISSION_CHOICES = [
        ('products.view', 'Voir les produits'),
        ('products.edit', 'Créer/modifier les produits'),
        ('stock.view', 'Voir le stock'),
        ('stock.adjust', 'Ajuster le stock'),
        ('stock.transfer', 'Transférer le stock'),
        ('orders.view', 'Voir les commandes'),
        ('orders.manage', 'Gérer les commandes'),
        ('sales.view', 'Voir les ventes'),
        ('sales.create', 'Créer une vente'),
        ('invoicing.view', 'Voir les factures'),
        ('invoicing.create', 'Créer des factures'),
        ('customers.view', 'Voir les clients'),
        ('finances.view', 'Voir les finances'),
    ]
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='roles')
    name = models.CharField(max_length=100)
    permissions = models.JSONField(default=list, help_text='Liste des codes de permission')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('store', 'name')

    def __str__(self):
        return f"{self.name} ({self.store.name})"

    def has_permission(self, code):
        return code in self.permissions


class StoreMember(models.Model):
    """Employé d'une boutique avec un rôle"""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='store_memberships')
    role = models.ForeignKey(StoreRole, on_delete=models.SET_NULL, null=True, blank=True)
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.SET_NULL, null=True, blank=True,
                                  help_text='Si défini, limité à cet entrepôt')
    salary = models.DecimalField(max_digits=12, decimal_places=0, default=0, help_text='Salaire mensuel brut (F)')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('store', 'user')

    def __str__(self):
        return f"{self.user.display_name} @ {self.store.name}"

    def has_permission(self, code):
        if not self.is_active:
            return False
        return self.role and self.role.has_permission(code)

# ======== PAIE DES EMPLOYÉS ========

class Payslip(models.Model):
    """Bulletin de paie d'un employé"""
    STATUS_CHOICES = [
        ('draft', 'Brouillon'),
        ('validated', 'Validé'),
        ('paid', 'Payé'),
    ]
    reference = models.CharField(max_length=20, unique=True, editable=False)
    member = models.ForeignKey(StoreMember, on_delete=models.CASCADE, related_name='payslips')
    month = models.IntegerField()
    year = models.IntegerField()
    base_salary = models.DecimalField(max_digits=12, decimal_places=0)
    bonuses = models.DecimalField(max_digits=12, decimal_places=0, default=0, help_text='Primes')
    deductions = models.DecimalField(max_digits=12, decimal_places=0, default=0, help_text='Retenues')
    net_salary = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('member', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"{self.reference} — {self.member.user.display_name}"

    def save(self, *args, **kwargs):
        if not self.reference:
            count = Payslip.objects.count() + 1
            self.reference = f"PAY-{count:05d}"
        self.net_salary = self.base_salary + self.bonuses - self.deductions
        super().save(*args, **kwargs)

    def mark_paid(self):
        self.status = 'paid'
        self.paid_at = timezone.now()
        self.save()

    def calculate_totals(self):
        """Recalcule depuis les lignes de primes/retenues"""
        self.bonuses = sum(l.amount for l in self.lines.filter(line_type='bonus'))
        self.deductions = sum(l.amount for l in self.lines.filter(line_type='deduction'))
        self.net_salary = self.base_salary + self.bonuses - self.deductions
        self.save()


class PayslipLine(models.Model):
    """Ligne de prime ou retenue sur un bulletin"""
    TYPE_CHOICES = [
        ('bonus', 'Prime'),
        ('deduction', 'Retenue'),
    ]
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='lines')
    line_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    label = models.CharField(max_length=200, help_text='Ex: Prime de transport, Assurance maladie')
    amount = models.DecimalField(max_digits=12, decimal_places=0)

    def __str__(self):
        return f"{self.label} ({self.amount}F)"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.payslip.calculate_totals()
