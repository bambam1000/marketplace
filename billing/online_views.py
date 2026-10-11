"""Paiement en ligne : réglages du vendeur, page « Payer » du client, retour et notifications du prestataire."""
import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from config import secretbox
from store import access

from . import online
from .gateways import PROVIDERS, GatewayError, client_for
from .models import OnlinePayment, PaymentGateway

log = logging.getLogger(__name__)


# ─── Vendeur : connexion du compte de paiement ───

def _owner_store(request):
    store = access.acting_store(request.user)
    if store is None or not access.has_perm(request.user, store, None):
        return None
    return store


# Ce que chaque prestataire demande au vendeur
PROVIDER_FORM = {
    'notchpay': {
        'name': 'Notch Pay', 'key_label': 'Clé publique Notch Pay', 'key_prefixes': ('pk',),
        'key_placeholder': 'pk_test_… ou pk_…',
        'key_error': 'Collez la clé publique (elle commence par « pk_ » ou « pk. »), pas la clé privée.',
        'secret_label': 'Clé de hachage des notifications', 'secret_placeholder': 'hash key du webhook', 'secret_prefix': '',
    },
    'stripe': {
        'name': 'Stripe', 'key_label': 'Clé secrète Stripe', 'key_prefixes': ('sk_', 'rk_'),
        'key_placeholder': 'sk_test_… ou sk_live_…',
        'key_error': 'Collez la clé secrète (elle commence par « sk_ », ou « rk_ » pour une clé restreinte) : '
                     'la clé publiable « pk_ » ne suffit pas pour créer des paiements.',
        'secret_label': 'Secret de signature du webhook', 'secret_placeholder': 'whsec_…', 'secret_prefix': 'whsec_',
    },
}


@login_required
def gateway_settings(request, provider='notchpay'):
    if provider not in PROVIDER_FORM:
        return redirect('payments:settings')
    store = _owner_store(request)
    if store is None:
        messages.error(request, 'Seul le propriétaire de la boutique peut régler le paiement en ligne.')
        return redirect('dashboard:index')
    here = redirect('payments:settings_provider', provider=provider)
    gw = PaymentGateway.objects.filter(store=store, provider=provider).first()
    name = PROVIDER_FORM[provider]['name']
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save':
            return _save_keys(request, store, gw, provider)
        if gw and action == 'disconnect':
            gw.public_key_enc = gw.webhook_secret_enc = gw.key_hint = ''
            gw.connected_at = None
            gw.save()
            messages.success(request, f"Compte {name} déconnecté : il n'est plus proposé à vos clients.")
        elif gw and action == 'toggle':
            gw.is_active = not gw.is_active
            gw.save(update_fields=['is_active'])
            messages.success(request, f'{name} ' + ('réactivé.' if gw.is_active else 'mis en pause : vos clients ne le voient plus.'))
        elif gw and gw.ready and action == 'test':
            try:
                pay = online.start_test(gw, request.user, _return_url(request, None))
            except GatewayError as exc:
                messages.error(request, f"Paiement d'essai impossible : {exc}")
            else:
                return redirect(pay.checkout_url)
        elif action == 'refresh':
            pay = OnlinePayment.objects.filter(store=store, pk=request.POST.get('payment')).first()
            if pay:
                try:
                    online.refresh(pay)
                    messages.success(request, f'{pay.reference} : {pay.get_status_display().lower()}.')
                except GatewayError as exc:
                    messages.error(request, str(exc))
        return here

    payments = OnlinePayment.objects.filter(store=store).select_related('order', 'gateway').order_by('-created_at')
    status = request.GET.get('status', '')
    if status in dict(OnlinePayment.STATUS_CHOICES):
        payments = payments.filter(status=status)
    since = timezone.now() - timedelta(days=30)
    stats = OnlinePayment.objects.filter(store=store, is_test=False, status='paid', paid_at__gte=since).aggregate(
        total=Sum('amount'), n=Count('id'))
    all_gws = {g.provider: g for g in PaymentGateway.objects.filter(store=store)}
    tabs = [(code, PROVIDER_FORM[code]['name'], all_gws.get(code)) for code in PROVIDER_FORM]
    return render(request, 'billing/online_settings.html', {
        'gw': gw, 'provider': provider, 'form': PROVIDER_FORM[provider], 'tabs': tabs, 'store': store,
        'page': Paginator(payments, 20).get_page(request.GET.get('page')), 'status': status,
        'statuses': OnlinePayment.STATUS_CHOICES, 'stats': stats,
        'test_amount': PROVIDERS[provider].test_amount,
        'webhook_url': request.build_absolute_uri(reverse('payments_webhook', args=[provider, gw.webhook_token])) if gw else '',
    })


def _save_keys(request, store, gw, provider):
    form = PROVIDER_FORM[provider]
    here = redirect('payments:settings_provider', provider=provider)
    key = (request.POST.get('public_key') or '').strip()
    secret = (request.POST.get('webhook_secret') or '').strip()
    gw = gw or PaymentGateway(store=store, provider=provider)
    if key:
        if not key.startswith(form['key_prefixes']):
            messages.error(request, form['key_error'])
            return here
        try:
            PROVIDERS[provider](gw).check_keys(key)
        except GatewayError as exc:
            messages.error(request, f'Connexion refusée : {exc}')
            return here
        gw.public_key_enc, gw.key_hint = secretbox.encrypt(key), key[-4:]
        gw.is_test = 'test' in key.lower()
        gw.connected_at, gw.last_error, gw.is_active = timezone.now(), '', True
    elif not gw.public_key_enc:
        messages.error(request, f"Collez votre {form['key_label'][0].lower()}{form['key_label'][1:]}.")
        return here
    if secret:
        if form['secret_prefix'] and not secret.startswith(form['secret_prefix']):
            messages.error(request, f"{form['secret_label']} : il commence par « {form['secret_prefix']} ».")
            return here
        gw.webhook_secret_enc = secretbox.encrypt(secret)
    gw.save()
    if key:
        messages.success(request, f"Compte {form['name']} connecté en mode TEST : faites un paiement d'essai, puis passez aux clés réelles."
                         if gw.is_test else f"Compte {form['name']} connecté : vos clients peuvent payer en ligne dès maintenant.")
    else:
        messages.success(request, 'Réglages enregistrés.')
    return here


def _return_url(request, pay):
    return request.build_absolute_uri(reverse('payments:return'))


# ─── Client : payer sa commande ───

def _buyer_order(request, order_number):
    from orders.models import Order
    return get_object_or_404(Order, order_number=order_number, buyer=request.user)


@login_required
def pay(request, order_number):
    order = _buyer_order(request, order_number)
    if request.method == 'POST':
        from store.models import Store
        store = get_object_or_404(Store, pk=request.POST.get('store'))
        try:
            pay = online.start(order, store, request.user, request.build_absolute_uri(reverse('payments:return')),
                               provider=request.POST.get('provider') or None,
                               cancel_url=request.build_absolute_uri(reverse('orders:pay', args=[order.order_number])))
        except GatewayError as exc:
            messages.error(request, str(exc))
            return redirect('orders:pay', order_number=order.order_number)
        request.session['last_payment'] = pay.reference
        return redirect(pay.checkout_url)
    # Paiements en attente : on demande leur état (le client revient peut-être de la page de paiement)
    for p in order.online_payments.filter(status='pending').exclude(checkout_url='')[:5]:
        try:
            online.refresh(p)
        except GatewayError:
            pass
    order.refresh_from_db()
    parts = online.order_parts(order)
    open_ = order.status in online.OPEN_ORDER and order.payment_method == 'online'
    return render(request, 'billing/pay.html', {
        'order': order, 'parts': parts, 'can_pay': open_ and not order.is_paid,
        'has_stripe': any(o['provider'] == 'stripe' for p in parts for o in p['options']),
        'left': sum(p['amount'] for p in parts if not p['paid']), 'n_left': sum(1 for p in parts if not p['paid']),
        # Paiement commencé il y a moins de 10 min : la page se recharge pour afficher la confirmation
        'waiting': any(p['last'] and not p['paid'] and p['last'].status == 'pending'
                       and p['last'].created_at > timezone.now() - timedelta(minutes=10) for p in parts),
    })


@login_required
def pay_return(request):
    """Retour depuis la page du prestataire. L'adresse ne prouve rien : on interroge le prestataire."""
    ref = request.GET.get('reference') or request.GET.get('trxref') or request.session.get('last_payment') or ''
    pay = (OnlinePayment.objects.filter(Q(reference=ref) | Q(provider_ref=ref)).filter(payer=request.user)
           .select_related('order', 'gateway').first() if ref else None)
    if pay is None:
        pay = OnlinePayment.objects.filter(payer=request.user).order_by('-created_at').first()
    if pay is None:
        return redirect('orders:list')
    try:
        online.refresh(pay)
    except GatewayError as exc:
        messages.warning(request, f'Nous n\'avons pas encore la confirmation du paiement ({exc}). Elle arrivera automatiquement.')
    if pay.status == 'paid':
        messages.success(request, f'Paiement de {online.amount_label(pay.amount)} FCFA confirmé. Merci !')
    elif pay.status == 'pending':
        messages.info(request, 'Paiement en cours de confirmation : validez-le sur votre téléphone si on vous le demande. Cette page se met à jour.')
    else:
        messages.error(request, f'Le paiement n\'a pas abouti ({pay.get_status_display().lower()}). Vous pouvez réessayer.')
    if pay.is_test:
        return redirect('payments:settings')
    order = pay.order
    if order.is_paid:
        return redirect('orders:detail', order_number=order.order_number)
    return redirect('orders:pay', order_number=order.order_number)


# ─── Prestataire : notifications (serveur à serveur) ───

@csrf_exempt
@require_POST
def webhook(request, provider, token):
    gw = PaymentGateway.objects.filter(provider=provider, webhook_token=token).first()
    if gw is None or provider not in PROVIDERS:
        return HttpResponse(status=404)
    client = client_for(gw)
    raw = request.body
    signed = client.verify_webhook(raw, request.headers)
    if gw.webhook_secret_enc and not signed:
        log.warning('Notification %s refusée : signature invalide (boutique %s)', provider, gw.store_id)
        return HttpResponseBadRequest('signature')
    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        return HttpResponseBadRequest('json')
    gw.last_webhook_at = timezone.now()
    gw.save(update_fields=['last_webhook_at'])
    refs = client.webhook_references(payload) if isinstance(payload, dict) else []
    pay = OnlinePayment.objects.filter(gateway=gw).filter(Q(reference__in=refs) | Q(provider_ref__in=refs)).first() if refs else None
    if pay and pay.status != 'paid':
        # Même signée, la notification n'est qu'un signal : l'état est relu chez le prestataire
        try:
            online.refresh(pay)
        except GatewayError as exc:
            log.warning('Notification %s : vérification impossible (%s)', pay.reference, exc)
            return HttpResponse(status=503)
    return HttpResponse('ok')
