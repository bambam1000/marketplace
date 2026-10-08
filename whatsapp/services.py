"""Envoi et réception WhatsApp d'AfriMarket (Evolution API, mode Baileys).

Envoi :
- rien n'est envoyé si WHATSAPP_ENABLED est faux ou si aucun numéro n'est connecté (les emails restent envoyés) ;
- un message à un client part du numéro de SA boutique si elle est connectée, sinon du numéro AfriMarket ;
- les alertes aux vendeurs partent du numéro AfriMarket ;
- chaque message est mis en file (WhatsAppMessage) : la requête web ne l'envoie jamais elle-même.
  WHATSAPP_DELIVERY_MODE choisit qui l'envoie :
    'thread' : un fil d'arrière-plan juste après la transaction (par défaut, pratique en développement) ;
    'worker' : uniquement `python manage.py whatsapp_worker` (production, plusieurs processus web) ;
    'inline' : tout de suite, dans la requête (tests) ;
  le worker reprend aussi les échecs avec un délai croissant (1 min, 5 min, 15 min, 1 h, 6 h).
- un numéro qui a répondu STOP ne reçoit plus de message automatique.

Réception (webhook messages.upsert) :
- chaque message entrant rejoint la conversation du contact dans la boîte de réception WhatsApp ;
- STOP / ARRET désinscrit le numéro, START / REPRENDRE le réinscrit (avec message de confirmation) ;
- le propriétaire de la boutique est notifié (cloche).
"""
import base64
import logging
import re
import threading
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.urls import reverse
from django.utils import timezone

from .client import EvolutionClient, EvolutionError
from .models import WhatsAppChat, WhatsAppInstance, WhatsAppMessage, WhatsAppOptOut

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 6
RETRY_DELAYS = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1), timedelta(hours=6)]
STALE_SENDING = timedelta(minutes=5)
# Messages qui ignorent la désinscription : réponses à un client qui vient d'écrire, confirmation STOP/START, test
OPT_OUT_EXEMPT = {'reply', 'optout', 'test'}
STOP_WORDS = {'STOP', 'ARRET', 'ARRETER', 'DESINSCRIRE', 'DESABONNER', 'UNSUBSCRIBE', 'STOP WHATSAPP'}
START_WORDS = {'START', 'REPRENDRE', 'REINSCRIRE', 'SUBSCRIBE'}


def is_enabled():
    return bool(settings.WHATSAPP_ENABLED and settings.EVOLUTION_API_KEY)


def diagnose(client=None):
    """Vérifie la configuration pas à pas. Retourne une liste de {ok, label, help} (s'arrête au premier blocage)."""
    checks = []

    def add(ok, label, help_text=''):
        checks.append({'ok': ok, 'label': label, 'help': help_text})
        return ok

    if not add(bool(settings.WHATSAPP_ENABLED), 'WhatsApp activé',
               "WHATSAPP_ENABLED vaut 0. En développement, il suffit que deploy/evolution/.env existe "
               "(copie de .env.example) ; en production, définissez WHATSAPP_ENABLED=1."):
        return checks
    if not add(bool(settings.EVOLUTION_API_KEY), 'Clé Evolution API configurée',
               'Définissez EVOLUTION_API_KEY (même valeur que AUTHENTICATION_API_KEY dans deploy/evolution/.env).'):
        return checks
    if not add(bool(settings.WHATSAPP_WEBHOOK_SECRET), 'Secret du webhook configuré',
               'Définissez WHATSAPP_WEBHOOK_SECRET (AFRIMARKET_WEBHOOK_SECRET dans deploy/evolution/.env).'):
        return checks
    client = client or EvolutionClient(timeout=5)
    try:
        info = client.server_info()
    except EvolutionError as exc:
        if exc.status in (401, 403):
            add(True, f'Evolution API joignable sur {client.base_url}')
            add(False, 'Clé API acceptée', "La clé EVOLUTION_API_KEY ne correspond pas à AUTHENTICATION_API_KEY d'Evolution API.")
            return checks
        if exc.status == 'not_evolution':
            add(False, f'Evolution API joignable sur {client.base_url}',
                f"Un autre programme répond sur {client.base_url}. Arrêtez-le ou changez le port d'Evolution API "
                "(docker-compose.yml) et EVOLUTION_API_URL.")
        else:
            add(False, f'Evolution API joignable sur {client.base_url}',
                "Aucune réponse : Evolution API (le serveur WhatsApp) n'est pas démarré. Il tourne dans Docker : "
                "installez WSL puis Docker Desktop (PowerShell administrateur : « wsl --install », redémarrage, puis "
                "« winget install -e --id Docker.DockerDesktop »), puis lancez « docker compose up -d » dans deploy/evolution.")
        return checks
    add(True, f"Evolution API {info.get('version', '')} joignable sur {client.base_url}")
    try:
        key_ok = client.verify_key()
    except EvolutionError:
        key_ok = True  # route absente sur une autre version : on ne bloque pas
    add(key_ok, 'Clé API acceptée', "La clé EVOLUTION_API_KEY ne correspond pas à AUTHENTICATION_API_KEY d'Evolution API.")
    return checks


def normalize_phone(raw, default_country=None):
    """Numéro au format international sans « + » (ex. 237690000001), ou '' s'il est inutilisable."""
    country = default_country or settings.WHATSAPP_DEFAULT_COUNTRY_CODE
    number = re.sub(r'[^\d+]', '', str(raw or '')).strip()
    if not number:
        return ''
    if number.startswith('+'):
        number = number[1:]
    elif number.startswith('00'):
        number = number[2:]
    elif len(number) == 9 and number.startswith('6'):   # mobile camerounais saisi sans indicatif
        number = country + number
    return number if 9 <= len(number) <= 15 and number.isdigit() else ''


def store_instance_name(store):
    return f'store-{store.pk}'


def platform_instance():
    return WhatsAppInstance.objects.filter(store__isnull=True, name=settings.WHATSAPP_PLATFORM_INSTANCE).first()


def sender_for_store(store):
    """Numéro qui écrit aux clients d'une boutique : le sien s'il est connecté, sinon celui d'AfriMarket."""
    if store is not None:
        own = WhatsAppInstance.objects.filter(store=store, status='open').first()
        if own:
            return own
    platform = platform_instance()
    return platform if platform and platform.is_connected else None


def is_opted_out(number):
    return WhatsAppOptOut.objects.filter(number=normalize_phone(number)).exists()


def _site_link(path):
    return settings.SITE_URL.rstrip('/') + path


def _wants_whatsapp(user):
    if user is None:
        return True
    from messaging.models import NotificationPreference
    prefs = NotificationPreference.objects.filter(user=user).first()
    return prefs is None or prefs.whatsapp_orders


# ───────────────────────── File d'envoi ─────────────────────────
def queue_message(instance, number, purpose, body='', order=None, invoice=None, file_name='', chat=None,
                  sent_by=None, dispatch=True, with_chat=False):
    """Met un message en file. Il est envoyé après la transaction en cours (voir WHATSAPP_DELIVERY_MODE)."""
    to = normalize_phone(number)
    if not is_enabled() or instance is None or not to:
        return None
    if purpose not in OPT_OUT_EXEMPT and is_opted_out(to):
        return None
    if chat is None and with_chat:
        chat = WhatsAppChat.objects.get_or_create(instance=instance, number=to)[0]
    msg = WhatsAppMessage.objects.create(
        instance=instance, to_number=to, purpose=purpose, body=body, order=order, invoice=invoice,
        file_name=file_name, chat=chat, sent_by=sent_by, next_attempt_at=timezone.now(),
    )
    if chat is not None:
        _touch_chat(chat, body or file_name, incoming=False)
    if dispatch:
        transaction.on_commit(lambda: dispatch_message(msg.pk))
    return msg


def dispatch_message(message_pk):
    mode = settings.WHATSAPP_DELIVERY_MODE
    if mode == 'inline':
        deliver(message_pk)
    elif mode == 'thread':
        threading.Thread(target=_deliver_in_thread, args=(message_pk,), daemon=True,
                         name=f'whatsapp-{message_pk}').start()
    # 'worker' : le processus whatsapp_worker s'en charge


def _deliver_in_thread(message_pk):
    try:
        deliver(message_pk)
    finally:
        close_old_connections()


def _claim(message_pk):
    """Réserve un message pour un seul expéditeur (fil, worker ou relance) : True si on l'a obtenu."""
    return WhatsAppMessage.objects.filter(
        pk=message_pk, direction='out', status__in=['queued', 'failed'], attempts__lt=MAX_ATTEMPTS,
    ).update(status='sending', updated_at=timezone.now()) == 1


def _invoice_pdf_base64(invoice):
    from invoicing.pdf_generator import generate_invoice_pdf
    buffer = generate_invoice_pdf(invoice)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('ascii')


def deliver(message_pk, client=None):
    """Envoie un message en file. Ne lève jamais d'exception : un échec est replanifié."""
    if not _claim(message_pk):
        return WhatsAppMessage.objects.filter(pk=message_pk).first()
    msg = WhatsAppMessage.objects.select_related('instance', 'invoice').get(pk=message_pk)
    if msg.instance is None:
        msg.status, msg.error, msg.next_attempt_at = 'failed', 'Aucun numéro WhatsApp', None
        msg.save()
        return msg
    client = client or EvolutionClient()
    msg.attempts += 1
    try:
        if msg.invoice_id and msg.file_name:
            response = client.send_document(msg.instance.name, msg.to_number, _invoice_pdf_base64(msg.invoice),
                                            msg.file_name, caption=msg.body)
        else:
            response = client.send_text(msg.instance.name, msg.to_number, msg.body)
        msg.message_id = client.message_id(response)
        msg.status, msg.error, msg.next_attempt_at = 'sent', '', None
        msg.sent_at = timezone.now()
    except Exception as exc:  # erreur Evolution API, réseau, PDF illisible… : journalisée puis relancée
        msg.status = 'failed'
        msg.error = (str(exc) if isinstance(exc, EvolutionError) else f'{type(exc).__name__} : {exc}')[:1000]
        delay = RETRY_DELAYS[min(msg.attempts - 1, len(RETRY_DELAYS) - 1)]
        msg.next_attempt_at = timezone.now() + delay if msg.attempts < MAX_ATTEMPTS else None
        logger.warning('WhatsApp %s vers %s en échec (essai %s) : %s', msg.purpose, msg.to_number, msg.attempts, exc)
    msg.save()
    return msg


def process_queue(client=None, limit=50):
    """Un passage du worker : débloque les envois interrompus puis envoie ce qui est dû. Retourne (envoyés, traités)."""
    now = timezone.now()
    WhatsAppMessage.objects.filter(status='sending', updated_at__lt=now - STALE_SENDING).update(status='queued')
    due = list(WhatsAppMessage.objects.filter(
        direction='out', status__in=['queued', 'failed'], attempts__lt=MAX_ATTEMPTS,
        next_attempt_at__lte=now, instance__status='open',
    ).order_by('next_attempt_at').values_list('pk', flat=True)[:limit])
    results = [deliver(pk, client=client) for pk in due]
    return sum(1 for m in results if m and m.status == 'sent'), len(results)


def retry_failed(client=None):
    """Relance immédiate de tous les échecs (commande whatsapp_retry), sans attendre leur délai."""
    WhatsAppMessage.objects.filter(direction='out', status='failed', attempts__lt=MAX_ATTEMPTS).update(next_attempt_at=timezone.now())
    return process_queue(client=client, limit=500)


def sync_instances(client=None):
    """Met à jour le statut de chaque numéro (au cas où un webhook de déconnexion aurait été manqué)."""
    client = client or EvolutionClient()
    for instance in WhatsAppInstance.objects.exclude(status='close'):
        try:
            state = client.connection_state(instance.name)
        except EvolutionError:
            continue
        if state != instance.status:
            instance.status = state if state in dict(WhatsAppInstance.STATUS_CHOICES) else 'close'
            instance.save(update_fields=['status', 'updated_at'])


# ───────────────────────── Réception ─────────────────────────
def _plain(text):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z ]', '', text.upper()).strip()


MEDIA_LABELS = {
    'imageMessage': 'Photo', 'videoMessage': 'Vidéo', 'audioMessage': 'Message vocal',
    'documentMessage': 'Document', 'stickerMessage': 'Autocollant', 'locationMessage': 'Position',
    'contactMessage': 'Contact',
}


def extract_text(message):
    """Texte lisible d'un message Baileys (texte, légende de média, ou type de média)."""
    message = message or {}
    if message.get('conversation'):
        return message['conversation']
    if (message.get('extendedTextMessage') or {}).get('text'):
        return message['extendedTextMessage']['text']
    for kind, label in MEDIA_LABELS.items():
        if kind in message:
            caption = (message[kind] or {}).get('caption') or ''
            return f'{label} {caption}'.strip()
    return '[Message non pris en charge]'


def sender_number(key):
    """Numéro de l'expéditeur. Ignore groupes, statuts et identifiants masqués (@lid) sans numéro réel."""
    jid = key.get('remoteJid') or ''
    if '@lid' in jid:
        jid = key.get('remoteJidAlt') or key.get('senderPn') or ''
    if not jid.endswith('@s.whatsapp.net'):
        return ''
    return normalize_phone(jid.split('@')[0])


def _match_customer(instance, number):
    """Client AfriMarket ayant ce numéro (profil ou commande), en privilégiant les clients de la boutique."""
    from accounts.models import User
    from orders.models import Order
    orders = Order.objects.exclude(shipping_phone='').select_related('buyer').order_by('-created_at')
    if instance.store_id:
        orders = orders.filter(items__store=instance.store)
    for phone, buyer_id in orders.values_list('shipping_phone', 'buyer_id')[:2000]:
        if normalize_phone(phone) == number:
            return User.objects.filter(pk=buyer_id).first()
    for user in User.objects.exclude(phone='').only('pk', 'phone')[:5000]:
        if normalize_phone(user.phone) == number:
            return user
    return None


def _touch_chat(chat, text, incoming):
    chat.last_message_at = timezone.now()
    chat.last_preview = (text or '')[:200]
    if incoming:
        chat.unread_count += 1
    chat.save(update_fields=['last_message_at', 'last_preview', 'unread_count'])


def _set_opt_out(instance, number, opted_out):
    """Désinscrit / réinscrit un numéro, et met à jour la préférence des comptes qui l'utilisent."""
    from accounts.models import User
    from messaging.models import NotificationPreference
    if opted_out:
        WhatsAppOptOut.objects.get_or_create(number=number, defaults={'instance': instance})
    else:
        WhatsAppOptOut.objects.filter(number=number).delete()
    for user in User.objects.exclude(phone=''):
        if normalize_phone(user.phone) == number:
            prefs, _ = NotificationPreference.objects.get_or_create(user=user)
            prefs.whatsapp_orders = not opted_out
            prefs.save(update_fields=['whatsapp_orders'])


def handle_incoming(instance, data):
    """Traite un message reçu (webhook messages.upsert). Retourne le message journalisé, ou None s'il est ignoré."""
    key = data.get('key') or {}
    if key.get('fromMe'):
        return None
    number = sender_number(key)
    message_id = key.get('id') or ''
    if not number or not message_id:
        return None
    if WhatsAppMessage.objects.filter(direction='in', message_id=message_id, instance=instance).exists():
        return None  # webhook rejoué

    text = extract_text(data.get('message'))
    chat, created = WhatsAppChat.objects.get_or_create(instance=instance, number=number)
    if data.get('pushName'):
        chat.contact_name = str(data['pushName'])[:200]
    if chat.customer_id is None:
        chat.customer = _match_customer(instance, number)
    chat.save()
    msg = WhatsAppMessage.objects.create(
        instance=instance, chat=chat, direction='in', to_number=number, purpose='inbound',
        body=text[:4000], status='received', message_id=message_id,
    )
    _touch_chat(chat, text, incoming=True)

    keyword = _plain(text)
    if keyword in STOP_WORDS:
        _set_opt_out(instance, number, True)
        queue_message(instance, number, 'optout', chat=chat, body=(
            'Vous ne recevrez plus de messages automatiques de notre part sur WhatsApp. '
            'Envoyez START pour les réactiver.'))
    elif keyword in START_WORDS:
        _set_opt_out(instance, number, False)
        queue_message(instance, number, 'optout', chat=chat, body=(
            'C\'est noté : vous recevrez à nouveau le suivi de vos commandes sur WhatsApp. Envoyez STOP pour arrêter.'))
    elif instance.store_id:
        from messaging.utils import notify
        notify(instance.store.owner, 'system', f'Nouveau message WhatsApp de {chat.display_name}', text[:200],
               url=reverse('whatsapp:chat', args=[chat.pk]))
    return msg


# ───────────────────────── Messages métier ─────────────────────────
def _money(value):
    return f'{int(value or 0):,}'.replace(',', ' ')


def notify_order_placed(order):
    """Confirmation au client + alerte à chaque vendeur concerné."""
    if not is_enabled():
        return
    items = list(order.items.select_related('product', 'store'))
    stores = {item.store for item in items}

    # Client : numéro de la boutique si la commande ne concerne qu'elle, sinon AfriMarket
    if _wants_whatsapp(order.buyer):
        sender = sender_for_store(next(iter(stores))) if len(stores) == 1 else sender_for_store(None)
        lines = '\n'.join(f'• {i.product.name} ×{i.quantity} — {_money(i.price * i.quantity)} FCFA' for i in items[:10])
        if len(items) > 10:
            lines += f'\n… et {len(items) - 10} autre(s) article(s)'
        body = (
            f'Bonjour {order.shipping_name or order.buyer.display_name} 👋\n\n'
            f'Merci pour votre commande *{order.order_number}* sur AfriMarket.\n\n'
            f'{lines}\n\n'
            f'Total : *{_money(order.total_amount)} FCFA*\n'
            f'Livraison : {order.shipping_city}\n\n'
            f'Suivre votre commande : {_site_link(reverse("orders:detail", args=[order.order_number]))}\n\n'
            f'Répondez STOP pour ne plus recevoir ces messages.'
        )
        number = order.shipping_phone or order.buyer.phone
        queue_message(sender, number, 'order_placed', body, order=order, with_chat=True)

    # Vendeurs : alerte depuis le numéro AfriMarket
    platform = sender_for_store(None)
    for store in stores:
        store_items = [i for i in items if i.store_id == store.pk]
        amount = sum(i.price * i.quantity for i in store_items)
        body = (
            f'🛒 Nouvelle commande *{order.order_number}* pour {store.name}\n\n'
            + '\n'.join(f'• {i.product.name} ×{i.quantity}' for i in store_items[:10])
            + f'\n\nMontant : *{_money(amount)} FCFA*\nClient : {order.shipping_name} ({order.shipping_city})\n\n'
            f'Gérer la commande : {_site_link(reverse("dashboard:order_detail", args=[order.order_number]))}'
        )
        queue_message(platform, store.whatsapp or store.phone, 'seller_new_order', body, order=order)


STATUS_MESSAGES = {
    'confirmed': '✅ Votre commande *{n}* est confirmée.',
    'processing': '📦 Votre commande *{n}* est en préparation.',
    'shipped': '🚚 Votre commande *{n}* a été expédiée.',
    'delivered': '🎉 Votre commande *{n}* a été livrée. Merci pour votre confiance !',
    'cancelled': '❌ Votre commande *{n}* a été annulée.',
}


def notify_order_status(order, new_status, store=None):
    """Suivi de commande au client, depuis le numéro de la boutique qui met à jour le statut."""
    if not is_enabled() or new_status not in STATUS_MESSAGES or not _wants_whatsapp(order.buyer):
        return None
    if order.is_direct_sale:
        return None  # vente au comptoir : le client est déjà en boutique
    body = STATUS_MESSAGES[new_status].format(n=order.order_number)
    if new_status == 'shipped' and order.tracking_number:
        body += f'\nNuméro de suivi : *{order.tracking_number}*'
    body += f'\n\nDétails : {_site_link(reverse("orders:detail", args=[order.order_number]))}'
    if store is None:
        first = order.items.select_related('store').first()
        store = first.store if first else None
    sender = sender_for_store(store)
    number = order.shipping_phone or order.buyer.phone
    return queue_message(sender, number, 'order_status', body, order=order, with_chat=True)


def send_invoice(invoice):
    """Facture PDF au client, depuis le numéro de la boutique."""
    if not is_enabled():
        return None
    if invoice.customer_id and not _wants_whatsapp(invoice.customer):
        return None
    number = invoice.customer_phone or (invoice.order.shipping_phone if invoice.order_id else '')
    sender = sender_for_store(invoice.store)
    body = (f'Bonjour {invoice.customer_name},\n'
            f'Voici votre facture *{invoice.invoice_number}* de {_money(invoice.total_amount)} FCFA — {invoice.store.name}.')
    return queue_message(sender, number, 'invoice', body, order=invoice.order, invoice=invoice,
                         file_name=f'Facture_{invoice.invoice_number}.pdf', with_chat=True)


# ───────────────────────── Catalogue des services (page Connexion) ─────────────────────────
SERVICE_GROUPS = [
    ('auto', 'Messages automatiques', 'fa-paper-plane'),
    ('conversations', 'Conversations', 'fa-comments'),
    ('reliability', 'Fiabilité', 'fa-shield-halved'),
]
SERVICE_GROUP_OF = {
    'fa-cart-shopping': 'auto', 'fa-truck-fast': 'auto', 'fa-file-invoice': 'auto', 'fa-bell': 'auto',
    'fa-store': 'auto', 'fa-life-ring': 'auto',
    'fa-comments': 'conversations', 'fa-bell-slash': 'conversations',
    'fa-check-double': 'reliability', 'fa-rotate': 'reliability',
}


def group_catalog(catalog):
    """Services rangés par colonne : [{key, label, icon, items, active}] (colonnes vides omises)."""
    groups = []
    for key, label, icon in SERVICE_GROUPS:
        items = [s for s in catalog if s['group'] == key]
        if items:
            groups.append({'key': key, 'label': label, 'icon': icon, 'items': items,
                           'active': sum(1 for s in items if s['state'] == 'active')})
    return groups


def service_catalog(instance, store, is_platform):
    """Services WhatsApp disponibles, avec leur état et leur activité sur 30 jours.
    Chaque service : {icon, title, description, trigger, recipient, state ('active'/'inactive'), reason, count}."""
    from django.db.models import Count
    since = timezone.now() - timedelta(days=30)
    counts = {}
    if instance:
        counts = dict(instance.messages.filter(created_at__gte=since).values_list('purpose').annotate(n=Count('id')))
    connected = bool(instance and instance.is_connected)
    platform = platform_instance()
    platform_ok = bool(platform and platform.is_connected)
    not_connected = 'Connectez le numéro pour activer ce service.'

    def svc(icon, title, description, trigger, recipient, purposes=(), state=None, reason=''):
        active = connected if state is None else state
        return {
            'group': SERVICE_GROUP_OF.get(icon, 'reliability'),
            'icon': icon, 'title': title, 'description': description, 'trigger': trigger, 'recipient': recipient,
            # raison propre au service ; « non connecté » est affiché une seule fois en tête de la carte
            'state': 'active' if active else 'inactive', 'reason': '' if active else reason,
            'count': sum(counts.get(p, 0) for p in purposes) if purposes else None,
        }

    if is_platform:
        return [
            svc('fa-store', 'Alerte « nouvelle commande » aux vendeurs',
                'Chaque vendeur concerné reçoit le détail de sa part de la commande, le montant et le lien de gestion.',
                'À chaque commande passée', 'Numéro WhatsApp de la boutique', ['seller_new_order']),
            svc('fa-life-ring', 'Relais pour les boutiques non connectées',
                "Confirmation, suivi et factures des boutiques qui n'ont pas connecté leur WhatsApp partent de ce numéro.",
                'Commande, changement de statut, facture', 'Client', ['order_placed', 'order_status', 'invoice']),
            svc('fa-comments', 'Conversations AfriMarket',
                'Les réponses des clients à ce numéro arrivent dans Conversations ; vous y répondez depuis le dashboard.',
                'Message reçu', 'Équipe AfriMarket', ['inbound', 'reply']),
            svc('fa-bell-slash', 'Désinscription STOP / START',
                'Un contact qui écrit STOP ne reçoit plus de message automatique ; START le réinscrit.',
                'Mot-clé reçu', 'Contact', ['optout']),
            svc('fa-rotate', "File d'envoi et relances",
                'Les messages partent en arrière-plan et sont relancés automatiquement en cas d’échec (jusqu’à 6 essais).',
                'En continu', '—', state=is_enabled(), reason="WhatsApp n'est pas activé sur ce serveur."),
        ]

    store_phone = (store.whatsapp or store.phone) if store else ''
    if not platform_ok:
        alert_state, alert_reason = False, "Nécessite le numéro AfriMarket (géré par l'administrateur)."
    elif not normalize_phone(store_phone):
        alert_state, alert_reason = False, 'Renseignez le numéro WhatsApp de la boutique dans ses paramètres.'
    else:
        alert_state, alert_reason = True, ''
    return [
        svc('fa-cart-shopping', 'Confirmation de commande',
            'Le client reçoit la liste des articles, le total, la ville de livraison et le lien de suivi.',
            'Commande passée', 'Client', ['order_placed']),
        svc('fa-truck-fast', 'Suivi de commande',
            'Confirmée, en préparation, expédiée (avec le numéro de suivi), livrée ou annulée.',
            'Changement de statut', 'Client', ['order_status']),
        svc('fa-file-invoice', 'Facture en PDF',
            'La facture est envoyée en pièce jointe PDF, en plus de l’email.',
            'Envoi d’une facture', 'Client', ['invoice']),
        svc('fa-bell', 'Alerte nouvelle commande',
            f'Vous êtes prévenu sur {("+" + normalize_phone(store_phone)) if normalize_phone(store_phone) else "le WhatsApp de la boutique"} avec le détail et le lien de gestion.',
            'Commande passée', 'Vous (boutique)', ['seller_new_order'], state=alert_state, reason=alert_reason),
        svc('fa-comments', 'Conversations et réponses',
            'Les messages de vos clients arrivent dans Conversations, client reconnu et commandes affichées ; vous répondez depuis le dashboard.',
            'Message reçu', 'Vous et vos employés (gestion des commandes)', ['inbound', 'reply']),
        svc('fa-bell-slash', 'Désinscription STOP / START',
            'Un client qui écrit STOP ne reçoit plus de message automatique ; START le réinscrit. Respecté partout.',
            'Mot-clé reçu', 'Client', ['optout']),
        svc('fa-check-double', 'Accusés de réception et de lecture',
            'Chaque message suit les statuts Envoyé, Reçu puis Lu, visibles dans les conversations et l’historique.',
            'Automatique', '—'),
        svc('fa-rotate', "File d'envoi et relances",
            'Les messages partent en arrière-plan sans ralentir le site, et sont relancés en cas d’échec (jusqu’à 6 essais).',
            'En continu', '—'),
    ]


def activity_stats(instance):
    """Activité des 30 derniers jours : envoyés, taux de lecture, échecs, non lus."""
    from django.db.models import Count, Q, Sum
    if not instance:
        return {'sent': 0, 'read_rate': None, 'failed': 0, 'pending': 0, 'unread': 0, 'chats': 0}
    since = timezone.now() - timedelta(days=30)
    out = instance.messages.filter(direction='out', created_at__gte=since)
    agg = out.aggregate(
        sent=Count('id', filter=Q(status__in=['sent', 'delivered', 'read'])),
        read=Count('id', filter=Q(status='read')),
        failed=Count('id', filter=Q(status='failed', next_attempt_at__isnull=True)),
        pending=Count('id', filter=Q(status__in=['queued', 'sending']) | Q(status='failed', next_attempt_at__isnull=False)),
    )
    chats = instance.chats.aggregate(n=Count('id'), unread=Sum('unread_count'))
    return {
        'sent': agg['sent'], 'read_rate': round(agg['read'] * 100 / agg['sent']) if agg['sent'] else None,
        'failed': agg['failed'], 'pending': agg['pending'], 'unread': chats['unread'] or 0, 'chats': chats['n'],
    }
