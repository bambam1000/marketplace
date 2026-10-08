from django.conf import settings
from django.db import models


class WhatsAppInstance(models.Model):
    """Un numéro WhatsApp connecté à Evolution API (QR code scanné).
    store=None : numéro de la plateforme AfriMarket ; sinon numéro d'une boutique."""
    STATUS_CHOICES = [
        ('close', 'Déconnecté'),
        ('connecting', 'En attente du QR code'),
        ('open', 'Connecté'),
    ]
    store = models.OneToOneField('store.Store', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='whatsapp_instance')
    name = models.CharField(max_length=100, unique=True, help_text="Nom de l'instance côté Evolution API")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='close')
    phone = models.CharField(max_length=30, blank=True, help_text='Numéro connecté (format international)')
    profile_name = models.CharField(max_length=200, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Instance WhatsApp'

    def __str__(self):
        owner = self.store.name if self.store_id else 'AfriMarket'
        return f'{owner} — {self.get_status_display()}'

    @property
    def is_connected(self):
        return self.status == 'open'


class WhatsAppChat(models.Model):
    """Conversation WhatsApp entre un numéro connecté (boutique ou AfriMarket) et un contact."""
    instance = models.ForeignKey(WhatsAppInstance, on_delete=models.CASCADE, related_name='chats')
    number = models.CharField(max_length=30)
    contact_name = models.CharField(max_length=200, blank=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='whatsapp_chats', help_text='Client AfriMarket reconnu par son numéro')
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_preview = models.CharField(max_length=200, blank=True)
    unread_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('instance', 'number')
        ordering = ['-last_message_at']
        verbose_name = 'Conversation WhatsApp'

    def __str__(self):
        return f'{self.display_name} ({self.instance})'

    @property
    def display_name(self):
        if self.customer_id:
            return self.customer.display_name
        return self.contact_name or f'+{self.number}'


class WhatsAppOptOut(models.Model):
    """Numéro qui a répondu STOP : plus aucun message automatique ne lui est envoyé."""
    number = models.CharField(max_length=30, unique=True)
    instance = models.ForeignKey(WhatsAppInstance, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Désinscription WhatsApp'

    def __str__(self):
        return f'+{self.number}'


class WhatsAppMessage(models.Model):
    """Messages WhatsApp envoyés (file d'envoi + suivi des accusés) et reçus (boîte de réception)."""
    STATUS_CHOICES = [
        ('queued', 'En attente'),
        ('sending', 'En cours d\'envoi'),
        ('sent', 'Envoyé'),
        ('delivered', 'Reçu'),
        ('read', 'Lu'),
        ('failed', 'Échec'),
        ('received', 'Reçu du client'),
    ]
    # Ordre de progression : un accusé ne fait jamais reculer le statut
    STATUS_RANK = {'queued': 0, 'sending': 0, 'failed': 0, 'sent': 1, 'delivered': 2, 'read': 3}
    PURPOSE_CHOICES = [
        ('order_placed', 'Confirmation de commande'),
        ('order_status', 'Suivi de commande'),
        ('seller_new_order', 'Nouvelle commande (vendeur)'),
        ('invoice', 'Facture'),
        ('test', 'Message de test'),
        ('reply', 'Réponse'),
        ('optout', 'Confirmation STOP / START'),
        ('inbound', 'Message reçu'),
    ]
    DIRECTION_CHOICES = [('out', 'Envoyé'), ('in', 'Reçu')]

    instance = models.ForeignKey(WhatsAppInstance, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    chat = models.ForeignKey(WhatsAppChat, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES, default='out')
    to_number = models.CharField(max_length=30, help_text='Destinataire (envoi) ou expéditeur (réception)')
    purpose = models.CharField(max_length=30, choices=PURPOSE_CHOICES)
    body = models.TextField(blank=True)
    file_name = models.CharField(max_length=200, blank=True)
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='whatsapp_messages')
    invoice = models.ForeignKey('invoicing.Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='whatsapp_messages')
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued', db_index=True)
    message_id = models.CharField(max_length=100, blank=True, db_index=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True,
                                           help_text='Prochain essai (vide = plus de relance)')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Message WhatsApp'

    def __str__(self):
        return f'{self.get_purpose_display()} → {self.to_number} ({self.get_status_display()})'

    def advance_status(self, new_status):
        """Applique un accusé (sent → delivered → read) sans jamais revenir en arrière."""
        if self.STATUS_RANK.get(new_status, -1) > self.STATUS_RANK.get(self.status, -1):
            self.status = new_status
            return True
        return False
