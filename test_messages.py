"""
Script pour créer des messages de test
Exécuter avec: python manage.py shell < test_messages.py
"""
from django.contrib.auth import get_user_model
from messaging.models import Conversation, Message

User = get_user_model()

# Récupérer ou créer des utilisateurs
users = User.objects.all()[:2]

if len(users) >= 2:
    user1 = users[0]
    user2 = users[1]

    # Créer une conversation
    convo, created = Conversation.objects.get_or_create(
        buyer=user1,
        seller=user2
    )

    # Créer quelques messages
    Message.objects.create(
        conversation=convo,
        sender=user2,
        content="Bonjour ! J'ai une question sur vos produits."
    )

    Message.objects.create(
        conversation=convo,
        sender=user2,
        content="Est-ce que vous livrez à Yaoundé ?"
    )

    print(f"✅ Conversation créée entre {user1.display_name} et {user2.display_name}")
    print(f"✅ 2 messages non lus ajoutés pour {user1.display_name}")

    # Compter les messages non lus
    from django.db.models import Q
    user_conversations = Conversation.objects.filter(
        Q(buyer=user1) | Q(seller=user1)
    ).values_list('id', flat=True)

    unread_count = Message.objects.filter(
        conversation_id__in=user_conversations,
        is_read=False
    ).exclude(
        sender=user1
    ).count()

    print(f"📬 {user1.display_name} a {unread_count} message(s) non lu(s)")
else:
    print("❌ Pas assez d'utilisateurs pour créer une conversation de test")
    print("Créez d'abord au moins 2 utilisateurs dans le système")
