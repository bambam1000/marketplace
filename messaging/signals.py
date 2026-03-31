from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Message
from .utils import send_new_message_notification


@receiver(post_save, sender=Message)
def notify_new_message(sender, instance, created, **kwargs):
    """
    Signal qui envoie une notification email lorsqu'un nouveau message est créé
    """
    if created:
        # Envoyer l'email de notification dans un thread séparé
        # pour ne pas bloquer la requête
        try:
            from threading import Thread
            thread = Thread(target=send_new_message_notification, args=(instance,))
            thread.start()
        except Exception as e:
            print(f"Erreur lors de l'envoi de la notification: {e}")
