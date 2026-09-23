from django.db import models
from django.conf import settings


class Conversation(models.Model):
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='buyer_conversations')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_conversations')
    product = models.ForeignKey('catalog.Product', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    @property
    def last_message(self):
        return self.messages.order_by('-created_at').first()


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username}: {self.content[:50]}"


class Notification(models.Model):
    """Notification interne pour un utilisateur"""
    TYPE_CHOICES = [
        ('order', 'Commande'),
        ('rfq', 'Devis'),
        ('invoice', 'Facture'),
        ('stock', 'Stock'),
        ('payment', 'Paiement'),
        ('account', 'Compte'),
        ('system', 'Système'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    notif_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='system')
    title = models.CharField(max_length=200)
    message = models.TextField()
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} — {self.title}"

    @property
    def icon(self):
        return {
            'order': 'fa-box',
            'rfq': 'fa-file-invoice',
            'invoice': 'fa-receipt',
            'stock': 'fa-boxes-stacked',
            'payment': 'fa-money-bill-wave',
            'account': 'fa-user',
            'system': 'fa-bell',
        }.get(self.notif_type, 'fa-bell')
