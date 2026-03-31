from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse


def send_new_message_notification(message):
    """
    Envoie une notification par email lorsqu'un nouveau message est reçu

    Args:
        message: Instance du modèle Message
    """
    conversation = message.conversation
    sender = message.sender

    # Déterminer le destinataire (celui qui n'a pas envoyé le message)
    if conversation.buyer == sender:
        recipient = conversation.seller
    else:
        recipient = conversation.buyer

    # Vérifier que le destinataire a un email
    if not recipient.email:
        return False

    # Construire l'URL de la conversation
    conversation_url = f"{settings.SITE_URL}{reverse('messaging:conversation', kwargs={'pk': conversation.pk})}"

    # Contexte pour le template
    context = {
        'recipient_name': recipient.display_name,
        'sender_name': sender.display_name,
        'message_content': message.content,
        'conversation_url': conversation_url,
        'site_url': settings.SITE_URL,
    }

    # Générer le contenu HTML et texte
    html_content = render_to_string('emails/new_message.html', context)
    text_content = render_to_string('emails/new_message.txt', context)

    # Créer l'email
    subject = f"💬 Nouveau message de {sender.display_name} - AfriMarket"

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=f"AfriMarket <{settings.DEFAULT_FROM_EMAIL}>",
        to=[recipient.email],
    )

    # Attacher la version HTML
    email.attach_alternative(html_content, "text/html")

    # Envoyer l'email
    try:
        email.send(fail_silently=False)
        return True
    except Exception as e:
        print(f"Erreur lors de l'envoi de l'email: {e}")
        return False


def send_new_message_notification_async(message):
    """
    Version asynchrone de l'envoi d'email (pour utilisation future avec Celery)
    Pour l'instant, appelle la fonction synchrone
    """
    return send_new_message_notification(message)
