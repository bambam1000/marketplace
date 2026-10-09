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
        ('message', 'Messages'),
        ('system', 'Système'),
    ]
    STYLES = {
        'order': ('fa-box', '#ff6a00'),
        'rfq': ('fa-file-signature', '#7a5af8'),
        'invoice': ('fa-file-invoice', '#2e90fa'),
        'stock': ('fa-boxes-stacked', '#f79009'),
        'payment': ('fa-money-bill-wave', '#12b76a'),
        'account': ('fa-user', '#475467'),
        'message': ('fa-comments', '#25a35a'),
        'system': ('fa-bell', '#667085'),
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    notif_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='system')
    title = models.CharField(max_length=200)
    message = models.TextField()
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'is_read', '-created_at'], name='notif_user_unread_idx')]

    def __str__(self):
        return f"{self.user.username} — {self.title}"

    @property
    def icon(self):
        return self.STYLES.get(self.notif_type, self.STYLES['system'])[0]

    @property
    def ago(self):
        """Temps écoulé court, en français : « à l'instant », « il y a 5 min », « hier »…"""
        from django.utils import timezone
        seconds = (timezone.now() - self.created_at).total_seconds()
        if seconds < 60:
            return "à l'instant"
        if seconds < 3600:
            return f'il y a {int(seconds // 60)} min'
        if seconds < 86400:
            return f'il y a {int(seconds // 3600)} h'
        days = (timezone.localdate() - timezone.localtime(self.created_at).date()).days
        if days == 1:
            return 'hier'
        if days < 7:
            return f'il y a {days} jours'
        return timezone.localtime(self.created_at).strftime('le %d/%m/%Y')

    @property
    def color(self):
        return self.STYLES.get(self.notif_type, self.STYLES['system'])[1]


class NotificationPreference(models.Model):
    """Préférences de notification par utilisateur"""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_prefs')
    # Notifications internes (cloche)
    internal_enabled = models.BooleanField(default=True, verbose_name="Notifications internes")
    # Emails par type d'événement
    email_order = models.BooleanField(default=True, verbose_name="Commandes")
    email_rfq = models.BooleanField(default=True, verbose_name="Devis")
    email_invoice = models.BooleanField(default=True, verbose_name="Factures")
    email_payment = models.BooleanField(default=True, verbose_name="Paiements")
    email_stock = models.BooleanField(default=False, verbose_name="Alertes stock")
    email_account = models.BooleanField(default=True, verbose_name="Compte")
    email_marketing = models.BooleanField(default=True, verbose_name="Offres et promotions des boutiques")
    whatsapp_orders = models.BooleanField(default=True, verbose_name="WhatsApp : commandes et factures")

    def __str__(self):
        return f"Préférences notif — {self.user.username}"

    # Réglages proposés à l'utilisateur : (champ, libellé, explication, pour les vendeurs seulement)
    FIELDS = [
        ('internal_enabled', 'Dans la cloche', 'Afficher les notifications sur le site et dans le tableau de bord', False),
        ('email_order', 'Commandes', 'Nouvelles commandes et suivi de vos commandes', False),
        ('email_rfq', 'Devis', 'Demandes de devis et offres reçues', False),
        ('email_invoice', 'Factures', 'Factures envoyées ou reçues', False),
        ('email_payment', 'Paiements', 'Confirmations de paiement et versements', False),
        ('email_account', 'Compte', 'Sécurité et informations de votre compte', False),
        ('email_stock', 'Alertes de stock', 'Produits qui passent sous leur seuil de stock', True),
        ('email_marketing', 'Offres des boutiques', 'Promotions envoyées par les boutiques où vous avez acheté', False),
        ('whatsapp_orders', 'WhatsApp', 'Confirmation et suivi de commande sur WhatsApp', False),
    ]

    def allows_email(self, notif_type):
        return {
            'order': self.email_order,
            'rfq': self.email_rfq,
            'invoice': self.email_invoice,
            'payment': self.email_payment,
            'stock': self.email_stock,
            'account': self.email_account,
        }.get(notif_type, True)
