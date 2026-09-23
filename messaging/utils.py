from django.core.mail import EmailMultiAlternatives, EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse


def notify(user, notif_type, title, message, url='', send_email=False, email_subject=None):
    """Crée une notification interne et envoie optionnellement un email brandé.

    Args:
        user: destinataire (User)
        notif_type: 'order', 'rfq', 'stock', 'payment', 'employee', 'system'...
        title: titre court
        message: texte de la notification
        url: lien interne (ex: reverse(...))
        send_email: envoyer aussi par email
        email_subject: sujet de l'email (défaut: title)
    """
    from .models import Notification, NotificationPreference
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)

    notif = None
    if prefs.internal_enabled:
        notif = Notification.objects.create(
            user=user, notif_type=notif_type, title=title, message=message, url=url
        )
    if send_email and user.email and prefs.allows_email(notif_type):
        try:
            html = render_to_string('emails/notification.html', {
                'user': user, 'title': title, 'message': message,
                'url': f"{settings.SITE_URL}{url}" if url else settings.SITE_URL,
            })
            email = EmailMessage(
                subject=email_subject or f'{title} — AfriMarket',
                body=html,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            email.content_subtype = 'html'
            email.send(fail_silently=True)
        except Exception:
            pass
    return notif


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
