# Configuration des notifications par email

## ✅ Système installé

Le système de notification par email est maintenant complètement installé et fonctionnel !

### Fichiers créés :

1. **Templates email** :
   - `templates/emails/new_message.html` - Version HTML (belle interface)
   - `templates/emails/new_message.txt` - Version texte simple

2. **Backend** :
   - `messaging/utils.py` - Fonction d'envoi d'email
   - `messaging/signals.py` - Signal Django pour envoi automatique
   - `messaging/apps.py` - Configuration de l'app avec signals

3. **Configuration** :
   - `config/settings.py` - Paramètres SMTP

## 📧 Fonctionnement

Lorsqu'un utilisateur reçoit un nouveau message :
1. Le signal Django détecte la création du message
2. Un email est envoyé automatiquement au destinataire
3. L'email contient :
   - Le nom de l'expéditeur
   - Le contenu du message
   - Un bouton pour répondre directement
   - Un lien vers la conversation

## 🔧 Configuration SMTP

### En développement (actuel)

Les emails sont affichés dans la console du serveur Django :
```python
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

Pour tester, regardez la console où tourne `python manage.py runserver`

### En production (Gmail)

Modifiez `config/settings.py` :

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'votre-email@gmail.com'
EMAIL_HOST_PASSWORD = 'votre-mot-de-passe-app'  # Pas le mot de passe normal !
DEFAULT_FROM_EMAIL = 'AfriMarket <votre-email@gmail.com>'
SITE_URL = 'https://votre-domaine.com'
```

**⚠️ Important pour Gmail** :
1. Activez la validation en 2 étapes
2. Créez un "Mot de passe d'application" :
   - Allez sur https://myaccount.google.com/apppasswords
   - Créez un mot de passe pour "Mail"
   - Utilisez ce mot de passe dans `EMAIL_HOST_PASSWORD`

### Autres fournisseurs SMTP

**Brevo (ex-Sendinblue)** - Gratuit jusqu'à 300 emails/jour :
```python
EMAIL_HOST = 'smtp-relay.brevo.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'votre-email@gmail.com'
EMAIL_HOST_PASSWORD = 'votre-clé-api-brevo'
```

**Mailgun** :
```python
EMAIL_HOST = 'smtp.mailgun.org'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'postmaster@votre-domaine.mailgun.org'
EMAIL_HOST_PASSWORD = 'votre-mot-de-passe-mailgun'
```

**Amazon SES** :
```python
EMAIL_BACKEND = 'django_ses.SESBackend'
AWS_ACCESS_KEY_ID = 'votre-access-key'
AWS_SECRET_ACCESS_KEY = 'votre-secret-key'
AWS_SES_REGION_NAME = 'us-east-1'
AWS_SES_REGION_ENDPOINT = 'email.us-east-1.amazonaws.com'
```

## 🧪 Tester le système

### Test manuel (console)

```bash
python manage.py shell
```

Puis :

```python
from django.contrib.auth import get_user_model
from messaging.models import Conversation, Message

User = get_user_model()
u1, u2 = User.objects.all()[:2]

# Créer une conversation si elle n'existe pas
c, _ = Conversation.objects.get_or_create(buyer=u1, seller=u2)

# Créer un message (déclenche l'envoi d'email)
Message.objects.create(
    conversation=c,
    sender=u2,
    content="Test de notification par email"
)
```

L'email sera affiché dans votre console de serveur Django.

### Test via l'interface

1. Connectez-vous avec 2 comptes différents
2. Envoyez un message depuis le compte A
3. Le compte B devrait recevoir un email
4. Vérifiez la console du serveur pour voir l'email

## 📱 Aperçu de l'email

L'email contient :
- **En-tête coloré** avec icône 💬
- **Message en surbrillance** avec bordure orange
- **Bouton CTA** "Répondre au message"
- **Footer** avec informations AfriMarket
- **Design responsive** qui s'adapte aux mobiles

## 🚀 Optimisations futures

Pour de meilleurs performances en production :

1. **Utiliser Celery** pour envoi asynchrone :
   ```python
   # Dans messaging/tasks.py
   from celery import shared_task

   @shared_task
   def send_email_task(message_id):
       message = Message.objects.get(id=message_id)
       send_new_message_notification(message)
   ```

2. **Limiter les notifications** :
   - Ne pas envoyer si l'utilisateur est en ligne
   - Regrouper plusieurs messages
   - Préférences utilisateur (activer/désactiver)

3. **Tracking** :
   - Ajouter des pixels de tracking
   - Suivre les ouvertures d'email
   - Analyser les clics

## 📝 Personnalisation

Pour modifier l'apparence de l'email, éditez :
- `templates/emails/new_message.html` - Design HTML
- `templates/emails/new_message.txt` - Version texte

Les variables disponibles :
- `{{ recipient_name }}` - Nom du destinataire
- `{{ sender_name }}` - Nom de l'expéditeur
- `{{ message_content }}` - Contenu du message
- `{{ conversation_url }}` - URL vers la conversation
- `{{ site_url }}` - URL du site

---

✅ **Le système est prêt à être utilisé !**

Pour toute question, consultez la documentation Django sur l'envoi d'emails :
https://docs.djangoproject.com/en/4.2/topics/email/
