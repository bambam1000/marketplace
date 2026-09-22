from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class Transaction(models.Model):
    """Toutes les transactions financières de la plateforme"""
    TYPE_CHOICES = [
        ('sale', 'Vente'),
        ('commission', 'Commission plateforme'),
        ('payout', 'Versement vendeur'),
        ('subscription', 'Abonnement'),
        ('boost', 'Boost produit'),
        ('refund', 'Remboursement'),
        ('fee', 'Frais divers'),
    ]
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('completed', 'Complété'),
        ('failed', 'Échoué'),
        ('cancelled', 'Annulé'),
    ]
    transaction_id = models.CharField(max_length=30, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions')
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=0)
    commission_amount = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    net_amount = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_method = models.CharField(max_length=20, blank=True)
    reference = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.transaction_id} - {self.get_type_display()} - {self.amount}F"

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            self.transaction_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"
        if not self.net_amount:
            self.net_amount = self.amount - self.commission_amount
        super().save(*args, **kwargs)


class SellerWallet(models.Model):
    """Portefeuille vendeur — solde disponible"""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    pending_balance = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_earned = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_withdrawn = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_commission_paid = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet: {self.user.username} - {self.balance}F"


class PayoutRequest(models.Model):
    """Demande de versement vendeur"""
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('processing', 'En cours'),
        ('completed', 'Versé'),
        ('rejected', 'Rejeté'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payout_requests')
    amount = models.DecimalField(max_digits=15, decimal_places=0)
    payment_method = models.CharField(max_length=20, choices=[
        ('momo', 'MTN MoMo'), ('om', 'Orange Money'), ('bank', 'Virement bancaire'),
    ])
    account_details = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_notes = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payout: {self.user.username} - {self.amount}F ({self.status})"


class Expense(models.Model):
    """Dépenses de la plateforme (admin)"""
    CATEGORY_CHOICES = [
        ('hosting', 'Hébergement'),
        ('marketing', 'Marketing'),
        ('salary', 'Salaires'),
        ('logistics', 'Logistique'),
        ('office', 'Bureau'),
        ('software', 'Logiciels'),
        ('other', 'Autre'),
    ]
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=15, decimal_places=0)
    date = models.DateField()
    receipt = models.FileField(upload_to='receipts/', blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.description} - {self.amount}F"


class WarehouseExpense(models.Model):
    """Dépense liée à un entrepôt"""
    CATEGORY_CHOICES = [
        ('rent', 'Loyer'),
        ('salary', 'Salaires'),
        ('transport', 'Transport'),
        ('supplies', 'Fournitures'),
        ('utilities', 'Électricité/Eau'),
        ('marketing', 'Marketing'),
        ('other', 'Autre'),
    ]
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.CASCADE, related_name='expenses')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=15, decimal_places=0)
    date = models.DateField()
    receipt = models.FileField(upload_to='expenses/', blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.warehouse.code} — {self.description} ({self.amount}F)"


class PlatformStats(models.Model):
    """Snapshot quotidien des stats plateforme"""
    date = models.DateField(unique=True)
    total_revenue = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_commissions = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_subscriptions = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_boosts = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_payouts = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    total_expenses = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    net_profit = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    active_sellers = models.IntegerField(default=0)
    active_buyers = models.IntegerField(default=0)
    new_orders = models.IntegerField(default=0)
    new_users = models.IntegerField(default=0)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"Stats: {self.date}"
