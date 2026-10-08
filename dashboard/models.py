from django.conf import settings
from django.db import models


class AssistantConversation(models.Model):
    """Conversation avec l'assistant IA (Claude) d'une boutique.

    `api_messages` est l'historique envoyé à l'API, tel que renvoyé par elle (blocs de réflexion compris) :
    il n'est jamais modifié, seulement complété, comme l'exige l'API pour garder la réflexion valide.
    `transcript` est la version lisible affichée dans le dashboard.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistant_conversations')
    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, null=True, blank=True,
                              related_name='assistant_conversations')
    title = models.CharField(max_length=200, blank=True)
    api_messages = models.JSONField(default=list)
    transcript = models.JSONField(default=list, help_text='[{role, text, tools, at}]')
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Conversation assistant IA'

    def __str__(self):
        return self.title or f'Conversation du {self.created_at:%d/%m/%Y}'
