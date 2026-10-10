from django.contrib import messages as flash
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from .models import Conversation, Message, Notification
from . import services


def _layout(user):
    """Vendeurs, administrateurs et employés restent dans le tableau de bord ; les acheteurs sur le site."""
    from store import access
    if access.is_admin(user) or user.is_seller or access.acting_store(user) is not None:
        return 'dashboard/base.html'
    return 'base.html'


def _conversation_or_404(user, pk):
    convo = get_object_or_404(Conversation.objects.select_related('buyer', 'seller', 'product'), pk=pk)
    if not services.can_access(user, convo):
        raise Http404
    return convo


def _wants_json(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('accept', '')


def _messenger(request, convo=None):
    query = request.GET.get('q', '').strip()[:80]
    only_unread = request.GET.get('filtre') == 'non-lus'
    conversations = list(services.inbox(request.user, query, only_unread))
    for c in conversations:
        c.other = services.counterpart(request.user, c)
        c.when = services.short_when(c.last_at)
        c.last_mine = c.last_sender == request.user.pk or (
            not services.is_buyer_side(request.user, c) and c.last_sender != c.buyer_id)
    context = {
        'layout': _layout(request.user), 'conversations': conversations, 'q': query, 'only_unread': only_unread,
        'conversation': convo, 'max_length': services.MAX_LENGTH,
        'total_unread': services.unread_count(request.user),
    }
    if convo is not None:
        msgs = list(convo.messages.select_related('sender'))
        context.update(
            other=services.counterpart(request.user, convo),
            messages_list=[services.message_payload(m, request.user, convo) | {'obj': m} for m in msgs],
            last_id=msgs[-1].pk if msgs else 0,
        )
    return render(request, 'messaging/messenger.html', context)


@login_required
def inbox(request):
    return _messenger(request)


@login_required
def conversation(request, pk):
    convo = _conversation_or_404(request.user, pk)
    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if not content:
            if _wants_json(request):
                return JsonResponse({'error': 'Le message est vide.'}, status=400)
            return redirect('messaging:conversation', pk=convo.pk)
        if len(content) > services.MAX_LENGTH:
            error = f'Message trop long ({len(content)} caractères, {services.MAX_LENGTH} au maximum).'
            if _wants_json(request):
                return JsonResponse({'error': error}, status=400)
            flash.error(request, error)
            return redirect('messaging:conversation', pk=convo.pk)
        msg = Message.objects.create(conversation=convo, sender=request.user, content=content)
        convo.save(update_fields=['updated_at'])
        if _wants_json(request):
            return JsonResponse({'message': services.message_payload(msg, request.user, convo)})
        return redirect('messaging:conversation', pk=convo.pk)  # pas de renvoi du formulaire au rechargement

    services.incoming(request.user, Conversation.objects.filter(pk=convo.pk)).filter(is_read=False).update(is_read=True)
    return _messenger(request, convo)


@login_required
def new_conversation(request, seller_id):
    """Contacter une boutique (depuis un produit ou une boutique) ou un client (depuis le tableau de bord)."""
    other = get_object_or_404(User, pk=seller_id, is_active=True)
    product = None
    raw = request.GET.get('product', '')
    if raw.isdigit():
        from catalog.models import Product
        product = Product.objects.filter(pk=raw).select_related('store').first()
    convo = services.start_conversation(request.user, other, product)
    if convo is None:
        flash.error(request, "Impossible d'ouvrir cette conversation.")
        return redirect('messaging:inbox')
    return redirect('messaging:conversation', pk=convo.pk)


@login_required
def get_unread_count(request):
    """Nombre de messages non lus (badge de l'en-tête)."""
    return JsonResponse({'unread_count': services.unread_count(request.user)})


@login_required
def get_new_messages(request, conversation_id):
    """Messages arrivés depuis `last_id`, et le dernier de mes messages lu par l'autre côté."""
    convo = Conversation.objects.filter(pk=conversation_id).select_related('buyer', 'seller').first()
    if convo is None or not services.can_access(request.user, convo):
        return JsonResponse({'error': 'Conversation introuvable'}, status=404)
    raw = request.GET.get('last_id', '0')
    last_id = int(raw) if raw.isdigit() else 0
    new = list(convo.messages.filter(pk__gt=last_id).select_related('sender'))
    incoming = services.incoming(request.user, Conversation.objects.filter(pk=convo.pk))
    incoming.filter(is_read=False).update(is_read=True)
    payload = [services.message_payload(m, request.user, convo) for m in new]
    mine_read = (convo.messages.filter(is_read=True).exclude(pk__in=incoming.values('pk'))
                 .order_by('-pk').values_list('pk', flat=True).first() or 0)
    return JsonResponse({'messages': payload, 'count': len(payload), 'read_upto': mine_read,
                         'unread_total': services.unread_count(request.user)})


# ========== NOTIFICATIONS ==========
def _safe_internal_url(url):
    """Lien interne d'une notification, seulement s'il mène à une page existante du site."""
    from django.urls import Resolver404, resolve
    from django.utils import translation
    if not url or not url.startswith('/') or url.startswith('//'):
        return None
    path = url.split('?')[0].split('#')[0]
    lang = translation.get_language() or 'fr'
    candidates = [path] if path.startswith(f'/{lang}/') or path.startswith('/static/') else [f'/{lang}{path}', path]
    for candidate in candidates:
        try:
            resolve(candidate)
            return url
        except Resolver404:
            continue
    return None


def _day_group(dt):
    from datetime import timedelta
    from django.utils import timezone
    d = timezone.localtime(dt).date()
    today = timezone.localdate()
    if d == today:
        return "Aujourd'hui"
    if d == today - timedelta(days=1):
        return 'Hier'
    if d > today - timedelta(days=7):
        return 'Cette semaine'
    if d > today - timedelta(days=31):
        return 'Ce mois-ci'
    return 'Plus ancien'


def notification_center(request, layout=None):
    """Centre de notifications (site et tableau de bord) : filtres, regroupement par jour, pagination."""
    from django.core.paginator import Paginator
    from django.db.models import Count, Q
    base = Notification.objects.filter(user=request.user)
    counts = dict(base.values_list('notif_type').annotate(n=Count('id')))
    unread_by_type = dict(base.filter(is_read=False).values_list('notif_type').annotate(n=Count('id')))
    current_type = request.GET.get('type', '')
    only_unread = request.GET.get('filtre') == 'non-lues'
    qs = base
    if current_type in counts:
        qs = qs.filter(notif_type=current_type)
    else:
        current_type = ''
    if only_unread:
        qs = qs.filter(is_read=False)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    groups = []
    for n in page.object_list:
        label = _day_group(n.created_at)
        if not groups or groups[-1][0] != label:
            groups.append((label, []))
        groups[-1][1].append(n)
    labels = dict(Notification.TYPE_CHOICES)
    types = [(code, labels.get(code, code), counts[code], unread_by_type.get(code, 0),
              Notification.STYLES.get(code, Notification.STYLES['system']))
             for code, _ in Notification.TYPE_CHOICES if code in counts]
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'messaging/notifications.html', {
        'layout': layout or _layout(request.user), 'page': page, 'groups': groups, 'types': types,
        'current_type': current_type, 'only_unread': only_unread, 'querystring': query.urlencode(),
        'total': sum(counts.values()), 'total_unread': sum(unread_by_type.values()),
        'read_count': sum(counts.values()) - sum(unread_by_type.values()),
        'prefs': _prefs_rows(request.user),
    })


def _prefs_rows(user):
    from .models import NotificationPreference
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return [{'field': f, 'label': label, 'help': help_text, 'on': getattr(prefs, f), 'email': f.startswith('email_')}
            for f, label, help_text, sellers_only in NotificationPreference.FIELDS if user.is_seller or not sellers_only]


def _notif_json(request, extra=None):
    data = {'unread': Notification.objects.filter(user=request.user, is_read=False).count()}
    data.update(extra or {})
    return JsonResponse(data)


@login_required
def notification_preferences(request):
    """Enregistre les préférences de notification (cases cochées = activé)."""
    from .models import NotificationPreference
    if request.method == 'POST':
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        for field, _label, _help, sellers_only in NotificationPreference.FIELDS:
            if sellers_only and not request.user.is_seller:
                continue
            setattr(prefs, field, field in request.POST)
        prefs.save()
        flash.success(request, 'Préférences de notification enregistrées.')
    return redirect(request.POST.get('next') or 'messaging:notifications')


@login_required
def notifications_list(request):
    return notification_center(request)


@login_required
def notification_mark_read(request, pk):
    """Ouvre une notification : la marque comme lue puis redirige vers sa page (si elle existe encore)."""
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.save(update_fields=['is_read'])
    target = _safe_internal_url(notif.url)
    if target is None:
        if notif.url:
            flash.info(request, "La page liée à cette notification n'existe plus.")
        return redirect('messaging:notifications')
    return redirect(target)


@login_required
def notification_action(request, pk):
    """POST action=read|unread|delete sur une notification."""
    if request.method != 'POST':
        return redirect('messaging:notifications')
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    action = request.POST.get('action')
    if action == 'delete':
        notif.delete()
    elif action in ('read', 'unread'):
        notif.is_read = action == 'read'
        notif.save(update_fields=['is_read'])
    if _wants_json(request):
        return _notif_json(request, {'ok': True, 'action': action})
    return redirect(request.POST.get('next') or 'messaging:notifications')


@login_required
def notifications_mark_all_read(request):
    """Marque toutes les notifications comme lues"""
    if request.method == 'POST':
        n = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        if _wants_json(request):
            return _notif_json(request, {'ok': True, 'marked': n})
        if n:
            flash.success(request, f'{n} notification{"s" if n > 1 else ""} marquée{"s" if n > 1 else ""} comme lue{"s" if n > 1 else ""}.')
    from urllib.parse import urlparse
    referer = urlparse(request.META.get('HTTP_REFERER', ''))
    same_site = not referer.netloc or referer.netloc == request.get_host()
    back = referer.path + (f'?{referer.query}' if referer.query else '') if same_site else ''
    return redirect(back if _safe_internal_url(back) else 'messaging:notifications')


@login_required
def notifications_delete_read(request):
    """Supprime les notifications déjà lues."""
    if request.method == 'POST':
        n, _ = Notification.objects.filter(user=request.user, is_read=True).delete()
        flash.success(request, f'{n} notification{"s" if n > 1 else ""} lue{"s" if n > 1 else ""} supprimée{"s" if n > 1 else ""}.')
    return redirect('messaging:notifications')


@login_required
def notifications_feed(request):
    """Cloche en direct : nombre de non lues et les non lues récentes (les lues n'y apparaissent plus)."""
    from django.urls import reverse
    latest = Notification.objects.filter(user=request.user, is_read=False)[:8]
    return _notif_json(request, {'items': [{
        'id': n.pk, 'title': n.title, 'message': n.message[:140], 'icon': n.icon, 'color': n.color,
        'read': n.is_read, 'ago': n.ago,
        'url': reverse('messaging:notification_read', args=[n.pk]),
    } for n in latest]})
