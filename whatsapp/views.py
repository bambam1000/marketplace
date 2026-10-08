import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from store import access

from . import services
from .client import EvolutionClient, EvolutionError
from .models import WhatsAppChat, WhatsAppInstance, WhatsAppMessage

logger = logging.getLogger(__name__)
MAX_WEBHOOK_BYTES = 1024 * 1024


# ───────────────────────── Accès ─────────────────────────
def _target(request):
    """Instance gérée par la page : celle de la boutique du propriétaire, ou celle d'AfriMarket (?scope=platform, admin).
    Retourne (store, nom_instance, est_plateforme) ou None si l'accès est refusé."""
    scope = request.GET.get('scope') or request.POST.get('scope')
    if scope == 'platform':
        if not access.is_admin(request.user):
            return None
        return None, settings.WHATSAPP_PLATFORM_INSTANCE, True
    store = request.user.store
    if store is None or not access.has_perm(request.user, store, None):
        return None
    return store, services.store_instance_name(store), False


def _get_instance(store, name):
    instance, _ = WhatsAppInstance.objects.get_or_create(name=name, defaults={'store': store})
    return instance


def _refresh(instance, client):
    """Synchronise le statut (et le numéro connecté) avec Evolution API."""
    state = client.connection_state(instance.name)
    _apply_state(instance, state)
    if state == 'open' and not instance.phone:
        info = client.fetch_instance(instance.name) or {}
        instance.phone = services.normalize_phone(str(info.get('ownerJid', '')).split('@')[0])
        instance.profile_name = (info.get('profileName') or '')[:200]
    instance.save()
    return state


def _apply_state(instance, state):
    if state not in dict(WhatsAppInstance.STATUS_CHOICES):
        state = 'close'
    if state == 'open' and instance.status != 'open':
        instance.connected_at = timezone.now()
    if state == 'close':
        instance.phone = ''
    instance.status = state


# ───────────────────────── Page du dashboard ─────────────────────────
@login_required
def whatsapp_settings(request):
    target = _target(request)
    if target is None:
        messages.error(request, "Seul le propriétaire de la boutique peut connecter WhatsApp.")
        return redirect('dashboard:index')
    store, name, is_platform = target
    instance = WhatsAppInstance.objects.filter(name=name).first()
    diagnostics = services.diagnose()
    ready = all(c['ok'] for c in diagnostics)
    api_error = None
    if ready and instance:
        try:
            _refresh(instance, EvolutionClient())
        except EvolutionError as exc:
            api_error = str(exc)
    logs = WhatsAppMessage.objects.filter(instance=instance).select_related('order', 'chat')[:8] if instance else []
    return render(request, 'whatsapp/settings.html', {
        'instance': instance,
        'store': store,
        'is_platform': is_platform,
        'scope': 'platform' if is_platform else '',
        'enabled': services.is_enabled(),
        'ready': ready,
        'diagnostics': diagnostics,
        'api_error': api_error,
        'logs': logs,
        'is_admin': access.is_admin(request.user),
        'platform': services.platform_instance(),
        'store_phone': (store.whatsapp or store.phone) if store else '',
        'catalog_groups': services.group_catalog(services.service_catalog(instance, store, is_platform)),
        'stats': services.activity_stats(instance),
    })


@login_required
@require_POST
def whatsapp_connect(request):
    """Crée l'instance si besoin et renvoie le QR code à scanner (JSON)."""
    target = _target(request)
    if target is None:
        return JsonResponse({'ok': False, 'error': 'Accès refusé'}, status=403)
    blocking = next((c for c in services.diagnose() if not c['ok']), None)
    if blocking:
        return JsonResponse({'ok': False, 'error': f"{blocking['label']} : non. {blocking['help']}"}, status=400)
    store, name, _ = target
    client = EvolutionClient()
    instance = _get_instance(store, name)
    try:
        if client.fetch_instance(name) is None:
            created = client.create_instance(name, settings.WHATSAPP_WEBHOOK_URL, settings.WHATSAPP_WEBHOOK_SECRET)
            qr = (created.get('qrcode') or {})
        else:
            # Instance déjà connue : on remet son webhook à jour (URL, secret, nouveaux événements)
            client.set_webhook(name, settings.WHATSAPP_WEBHOOK_URL, settings.WHATSAPP_WEBHOOK_SECRET)
            qr = client.connect(name)
        if (qr.get('instance') or {}).get('state') == 'open':
            _apply_state(instance, 'open')
            instance.save()
            return JsonResponse({'ok': True, 'state': 'open'})
        instance.status = 'connecting'
        instance.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'ok': True, 'state': 'connecting', 'qr': qr.get('base64', ''), 'pairing_code': qr.get('pairingCode') or ''})
    except EvolutionError as exc:
        logger.warning('Connexion WhatsApp %s : %s', name, exc)
        return JsonResponse({'ok': False, 'error': "Evolution API ne répond pas. Vérifiez qu'il est démarré."}, status=502)


@login_required
def whatsapp_status(request):
    """Statut de la connexion, et QR code à jour tant que le téléphone n'a pas scanné (sondé par la page)."""
    target = _target(request)
    if target is None:
        return JsonResponse({'ok': False}, status=403)
    store, name, _ = target
    instance = WhatsAppInstance.objects.filter(name=name).first()
    if instance is None or not services.is_enabled():
        return JsonResponse({'ok': True, 'state': 'close'})
    client = EvolutionClient()
    try:
        state = _refresh(instance, client)
        payload = {'ok': True, 'state': state, 'phone': instance.phone, 'profile': instance.profile_name}
        if state == 'connecting':
            payload['qr'] = (client.connect(name) or {}).get('base64', '')
        return JsonResponse(payload)
    except EvolutionError:
        return JsonResponse({'ok': False, 'state': instance.status, 'error': 'Evolution API injoignable'}, status=502)


@login_required
@require_POST
def whatsapp_disconnect(request):
    target = _target(request)
    if target is None:
        return HttpResponseForbidden()
    store, name, is_platform = target
    instance = WhatsAppInstance.objects.filter(name=name).first()
    if instance:
        try:
            EvolutionClient().logout(name)
        except EvolutionError as exc:
            logger.warning('Déconnexion WhatsApp %s : %s', name, exc)
        _apply_state(instance, 'close')
        instance.save()
        messages.success(request, 'WhatsApp déconnecté.')
    return redirect(_back(is_platform))


@login_required
@require_POST
def whatsapp_test(request):
    """Envoie un message de test depuis le numéro connecté."""
    target = _target(request)
    if target is None:
        return HttpResponseForbidden()
    store, name, is_platform = target
    instance = WhatsAppInstance.objects.filter(name=name, status='open').first()
    number = request.POST.get('number', '')
    if instance is None:
        messages.error(request, "Connectez d'abord WhatsApp.")
    elif not services.normalize_phone(number):
        messages.error(request, 'Numéro invalide. Exemple : 690 00 00 01 ou +237 690 00 00 01.')
    else:
        msg = services.queue_message(instance, number, 'test', dispatch=False, sent_by=request.user,
                                     body=f'✅ Test AfriMarket : WhatsApp est bien connecté pour {store.name if store else "AfriMarket"}.')
        if msg is not None:
            msg = services.deliver(msg.pk)  # test : envoi immédiat pour afficher le résultat tout de suite
        if msg is not None and msg.status == 'sent':
            messages.success(request, f'Message de test envoyé au {msg.to_number}.')
        else:
            messages.error(request, "Le message de test n'a pas pu être envoyé." + (f' ({msg.error[:120]})' if msg else ''))
    return redirect(_back(is_platform))


def _back(is_platform):
    from django.urls import reverse
    url = reverse('whatsapp:settings')
    return url + '?scope=platform' if is_platform else url


# ───────────────────────── Webhook Evolution API ─────────────────────────
ACK_STATUS = {'SERVER_ACK': 'sent', 'DELIVERY_ACK': 'delivered', 'READ': 'read', 'PLAYED': 'read'}


@csrf_exempt
@require_POST
def webhook(request):
    """Événements envoyés par Evolution API. Authentifiés par l'en-tête X-AfriMarket-Secret."""
    secret = settings.WHATSAPP_WEBHOOK_SECRET
    given = request.headers.get('X-AfriMarket-Secret', '')
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        return HttpResponseForbidden('Secret invalide')
    if len(request.body) > MAX_WEBHOOK_BYTES:
        return JsonResponse({'ok': False, 'error': 'Trop volumineux'}, status=413)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON invalide'}, status=400)

    event = str(payload.get('event', '')).lower().replace('_', '.')
    data = payload.get('data') or {}
    instance = WhatsAppInstance.objects.filter(name=payload.get('instance', '')).first()

    if event == 'connection.update' and instance and isinstance(data, dict):
        _apply_state(instance, data.get('state', 'close'))
        if instance.status == 'open':
            wuid = str(data.get('wuid') or '').split('@')[0]
            if wuid:
                instance.phone = services.normalize_phone(wuid)
            if data.get('profileName'):
                instance.profile_name = str(data['profileName'])[:200]
        instance.save()
    elif event == 'messages.upsert' and instance:
        for row in data if isinstance(data, list) else [data]:
            if isinstance(row, dict):
                services.handle_incoming(instance, row)
    elif event == 'messages.update':
        for row in data if isinstance(data, list) else [data]:
            if not isinstance(row, dict):
                continue
            new_status = ACK_STATUS.get(str(row.get('status', '')).upper())
            key_id = row.get('keyId') or (row.get('key') or {}).get('id')
            if not new_status or not key_id:
                continue
            for msg in WhatsAppMessage.objects.filter(message_id=key_id):
                if msg.advance_status(new_status):
                    msg.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'ok': True})


# ───────────────────────── Boîte de réception ─────────────────────────
def _inbox_instance(request):
    """Numéro dont on lit les conversations : celui de la boutique (propriétaire ou employé avec orders.manage),
    ou celui d'AfriMarket (?scope=platform, admin). Retourne (instance ou None, est_plateforme) ou None si refusé."""
    if (request.GET.get('scope') or request.POST.get('scope')) == 'platform':
        if not access.is_admin(request.user):
            return None
        return services.platform_instance(), True
    store = access.acting_store(request.user)
    if store is None or not access.has_perm(request.user, store, 'orders.manage'):
        return None
    return WhatsAppInstance.objects.filter(store=store).first(), False


def _chat_allowed(request, chat):
    if chat.instance.store_id is None:
        return access.is_admin(request.user)
    return access.has_perm(request.user, chat.instance.store, 'orders.manage')


@login_required
def inbox(request):
    target = _inbox_instance(request)
    if target is None:
        messages.error(request, "Vous n'avez pas accès aux conversations WhatsApp.")
        return redirect('dashboard:index')
    instance, is_platform = target
    chats = WhatsAppChat.objects.none()
    q = request.GET.get('q', '').strip()
    if instance:
        chats = instance.chats.select_related('customer').exclude(last_message_at__isnull=True)
        if q:
            digits = ''.join(ch for ch in q if ch.isdigit())
            cond = Q(contact_name__icontains=q) | Q(customer__first_name__icontains=q) | Q(customer__username__icontains=q)
            if digits:
                cond |= Q(number__contains=digits)
            chats = chats.filter(cond)
    return render(request, 'whatsapp/inbox.html', {
        'instance': instance,
        'chats': chats[:200],
        'search_query': q,
        'is_platform': is_platform,
        'scope': 'platform' if is_platform else '',
        'unread_total': instance.chats.aggregate(t=Sum('unread_count'))['t'] or 0 if instance else 0,
        'is_admin': access.is_admin(request.user),
    })


@login_required
def chat_detail(request, pk):
    chat = get_object_or_404(WhatsAppChat.objects.select_related('instance', 'instance__store', 'customer'), pk=pk)
    if not _chat_allowed(request, chat):
        messages.error(request, "Vous n'avez pas accès à cette conversation.")
        return redirect('dashboard:index')
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if not text:
            messages.error(request, 'Écrivez un message.')
        elif not chat.instance.is_connected:
            messages.error(request, "Le numéro WhatsApp n'est pas connecté : reconnectez-le pour répondre.")
        elif services.queue_message(chat.instance, chat.number, 'reply', body=text[:4000], chat=chat,
                                    sent_by=request.user) is None:
            messages.error(request, "Le message n'a pas pu être mis en file (WhatsApp désactivé ?).")
        return redirect('whatsapp:chat', pk=chat.pk)

    if chat.unread_count:
        chat.unread_count = 0
        chat.save(update_fields=['unread_count'])
    history = list(chat.messages.select_related('sent_by', 'order').order_by('-created_at')[:200])[::-1]
    orders = []
    if chat.customer_id:
        from orders.models import Order
        orders = Order.objects.filter(buyer=chat.customer)
        if chat.instance.store_id:
            orders = orders.filter(items__store=chat.instance.store).distinct()
        orders = orders.order_by('-created_at')[:5]
    return render(request, 'whatsapp/chat.html', {
        'chat': chat,
        'history': history,
        'orders': orders,
        'opted_out': services.is_opted_out(chat.number),
        'scope': '' if chat.instance.store_id else 'platform',
        'last_id': history[-1].pk if history else 0,
    })


@login_required
def chat_updates(request, pk):
    """Nouveaux messages et statuts à jour (sondé toutes les 5 s par la page de conversation)."""
    chat = get_object_or_404(WhatsAppChat.objects.select_related('instance'), pk=pk)
    if not _chat_allowed(request, chat):
        return JsonResponse({'ok': False}, status=403)
    try:
        after = int(request.GET.get('after', 0))
    except ValueError:
        after = 0
    new = list(chat.messages.filter(pk__gt=after).order_by('created_at')[:100])
    if new and any(m.direction == 'in' for m in new):
        WhatsAppChat.objects.filter(pk=chat.pk).update(unread_count=0)
    recent_out = chat.messages.filter(direction='out').order_by('-created_at')[:30]
    return JsonResponse({
        'ok': True,
        'messages': [_message_json(m) for m in new],
        'statuses': {m.pk: [m.status, m.get_status_display()] for m in recent_out},
    })


def _message_json(m):
    return {
        'id': m.pk, 'direction': m.direction, 'body': m.body or m.file_name, 'status': m.status,
        'status_label': m.get_status_display(), 'purpose': m.get_purpose_display(),
        'time': timezone.localtime(m.created_at).strftime('%d/%m %H:%M'),
    }
