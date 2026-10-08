from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Avg, F
from datetime import timedelta
from .models import PromoCode, Campaign, Newsletter, MarketingAnalytics, LoyaltyProgram
from catalog.models import Product
from orders.models import Order


def _parse_local_dt(value):
    """'AAAA-MM-JJTHH:MM' (champ datetime-local) -> datetime aware à l'heure de Douala, sinon None."""
    from datetime import datetime
    try:
        return timezone.make_aware(datetime.strptime((value or '').strip(), '%Y-%m-%dT%H:%M'))
    except ValueError:
        return None


def _store_products(store, ids):
    """Ne garde que les produits de la boutique (un id d'une autre boutique est ignoré)."""
    clean = [i for i in ids if str(i).isdigit()]
    return Product.objects.filter(store=store, pk__in=clean)


def _store_promo(store, promo_id):
    """Code promo de la boutique, ou None (un id d'une autre boutique est ignoré)."""
    if not promo_id or not str(promo_id).isdigit():
        return None
    return PromoCode.objects.filter(store=store, pk=promo_id).first()


def _site_url(request=None):
    """Adresse publique du site pour les liens des emails et messages (SITE_URL en production)."""
    from django.conf import settings as dj_settings
    if request is not None:
        return request.build_absolute_uri('/')[:-1]
    return dj_settings.SITE_URL.rstrip('/')


def _decimal(value, default=None):
    from decimal import Decimal, InvalidOperation
    if value is None or str(value).strip() == '':
        return default
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return 'invalid'


def _promo_from_post(request, store, promo_code=None):
    """Remplit un code promo depuis le formulaire. Retourne (code_promo, erreurs)."""
    import re
    from decimal import Decimal
    promo = promo_code or PromoCode(store=store)
    errors = []
    code = request.POST.get('code', '').strip().upper()
    promo.code = code
    promo.description = request.POST.get('description', '').strip()
    discount_type = request.POST.get('discount_type', 'percentage')
    promo.discount_type = discount_type if discount_type in dict(PromoCode.DISCOUNT_TYPE_CHOICES) else 'percentage'
    if not re.fullmatch(r'[A-Z0-9_-]{3,50}', code):
        errors.append('Le code doit contenir de 3 à 50 lettres, chiffres, tirets ou soulignés.')
    elif PromoCode.objects.filter(code=code).exclude(pk=promo.pk).exists():
        errors.append(f'Le code « {code} » est déjà utilisé sur la plateforme. Choisissez-en un autre.')

    value = _decimal(request.POST.get('discount_value'))
    if value in (None, 'invalid') or value <= 0:
        errors.append('La réduction doit être un nombre supérieur à 0.')
    elif promo.discount_type == 'percentage' and value > 100:
        errors.append('Une réduction en pourcentage ne peut pas dépasser 100 %.')
    else:
        promo.discount_value = value
    min_purchase = _decimal(request.POST.get('min_purchase_amount'), Decimal('0'))
    if min_purchase == 'invalid' or min_purchase < 0:
        errors.append("Le montant minimum d'achat est invalide.")
    else:
        promo.min_purchase_amount = min_purchase
    max_discount = _decimal(request.POST.get('max_discount_amount'))
    if max_discount == 'invalid' or (max_discount is not None and max_discount <= 0):
        errors.append('La réduction maximale doit être vide ou supérieure à 0.')
    else:
        promo.max_discount_amount = max_discount
    limit = request.POST.get('usage_limit', '').strip()
    if limit and (not limit.isdigit() or int(limit) < 1):
        errors.append("La limite d'utilisation doit être vide ou au moins 1.")
    else:
        promo.usage_limit = int(limit) if limit else None

    valid_from = _parse_local_dt(request.POST.get('valid_from')) or (promo.valid_from if promo.pk else timezone.now())
    valid_to = _parse_local_dt(request.POST.get('valid_to'))
    if valid_to is None:
        errors.append('La date de fin de validité est requise.')
    elif valid_to <= valid_from:
        errors.append('La date de fin doit être après la date de début.')
    promo.valid_from = valid_from
    if valid_to:
        promo.valid_to = valid_to
    promo.is_active = 'is_active' in request.POST
    return promo, errors


def _campaign_from_post(request, store, campaign=None):
    """Remplit une campagne depuis le formulaire. Retourne (campagne, erreurs, produits, code promo)."""
    from decimal import Decimal
    campaign = campaign or Campaign(store=store)
    errors = []
    campaign.name = request.POST.get('name', '').strip()[:200]
    campaign.description = request.POST.get('description', '').strip()
    ctype = request.POST.get('campaign_type', '')
    if not campaign.name:
        errors.append('Le nom de la campagne est requis.')
    if ctype not in dict(Campaign.CAMPAIGN_TYPE_CHOICES):
        errors.append('Type de campagne invalide.')
    else:
        campaign.campaign_type = ctype
    status = request.POST.get('status', 'scheduled')
    campaign.status = status if status in dict(Campaign.STATUS_CHOICES) else 'scheduled'
    budget = _decimal(request.POST.get('budget'), Decimal('0'))
    if budget == 'invalid' or budget < 0:
        errors.append('Le budget doit être un nombre positif.')
    else:
        campaign.budget = budget
    start = _parse_local_dt(request.POST.get('start_date'))
    end = _parse_local_dt(request.POST.get('end_date'))
    if not start or not end or end <= start:
        errors.append('Dates de campagne invalides : la fin doit être après le début.')
    else:
        campaign.start_date, campaign.end_date = start, end
    banner = request.FILES.get('banner')
    if banner:
        if not banner.name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')) or banner.size > 5 * 1024 * 1024:
            errors.append('Bannière refusée : image JPG, PNG, WEBP ou GIF de 5 Mo maximum.')
        else:
            campaign.banner = banner
    products = _store_products(store, request.POST.getlist('target_products'))
    promo = _store_promo(store, request.POST.get('promo_code'))
    return campaign, errors, products, promo


@login_required
def marketing_dashboard(request):
    """Dashboard marketing principal"""
    if not request.user.is_seller or not (request.user.store is not None):
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')

    store = request.user.store
    now = timezone.now()
    last_30_days = now - timedelta(days=30)

    # Statistiques générales
    active_campaigns = Campaign.objects.filter(store=store, status='active').count()
    active_promos = PromoCode.objects.filter(store=store, is_active=True).count()
    total_loyalty_members = LoyaltyProgram.objects.filter(store=store).count()
    emails_count = Newsletter.objects.filter(store=store).count()
    messaging_count = MessagingCampaign.objects.filter(store=store).count()
    facebook_count = FacebookPost.objects.filter(store=store).count()

    # 30 derniers jours : MarketingAnalytics n'est alimenté nulle part, on calcule depuis les vraies ventes
    paid_items = _store_paid_items(store, last_30_days)
    camp_totals = Campaign.objects.filter(store=store).aggregate(views=Sum('views_count'), clicks=Sum('clicks_count'))
    analytics = {
        'total_views': camp_totals['views'] or 0,
        'total_visitors': camp_totals['clicks'] or 0,
        'total_purchases': paid_items.values('order').distinct().count(),
        'total_revenue': paid_items.aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0,
    }

    # Campagnes récentes
    recent_campaigns = Campaign.objects.filter(store=store).order_by('-created_at')[:5]

    # Codes promo actifs
    active_promo_codes = PromoCode.objects.filter(store=store, is_active=True)[:10]

    context = {
        'active_campaigns': active_campaigns,
        'active_promos': active_promos,
        'total_loyalty_members': total_loyalty_members,
        'emails_count': emails_count,
        'messaging_count': messaging_count,
        'facebook_count': facebook_count,
        'analytics': analytics,
        'recent_campaigns': recent_campaigns,
        'active_promo_codes': active_promo_codes,
    }
    return render(request, 'marketing/dashboard.html', context)


def _store_paid_items(store, since):
    """Lignes de vente de la boutique, payées, hors commandes annulées ou remboursées, depuis `since`."""
    from orders.models import OrderItem
    return (OrderItem.objects.filter(store=store, order__is_paid=True, order__created_at__gte=since)
            .exclude(order__status__in=['cancelled', 'refunded']))


# ========== CODES PROMO ==========
@login_required
def promo_codes_list(request):
    """Liste des codes promo"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    store = request.user.store
    promo_codes = PromoCode.objects.filter(store=store).order_by('-created_at')

    active_count = promo_codes.filter(is_active=True).count()
    context = {
        'promo_codes': promo_codes,
        'active_count': active_count,
        'inactive_count': promo_codes.count() - active_count,
    }
    return render(request, 'marketing/promo_codes_list.html', context)


@login_required
def promo_code_create(request):
    """Créer un code promo"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    store = request.user.store

    if request.method == 'POST':
        promo, errors = _promo_from_post(request, store)
        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'marketing/promo_code_form.html', {'promo_code': promo})
        promo.save()
        messages.success(request, f'Code promo {promo.code} créé avec succès !')
        return redirect('marketing:promo_codes')

    return render(request, 'marketing/promo_code_form.html')


@login_required
def promo_code_edit(request, pk):
    """Modifier un code promo"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    promo_code = get_object_or_404(PromoCode, pk=pk, store=request.user.store)

    if request.method == 'POST':
        promo_code, errors = _promo_from_post(request, request.user.store, promo_code)
        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'marketing/promo_code_form.html', {'promo_code': promo_code})
        promo_code.save()
        messages.success(request, 'Code promo modifié !')
        return redirect('marketing:promo_codes')

    return render(request, 'marketing/promo_code_form.html', {'promo_code': promo_code})


@login_required
def promo_code_toggle(request, pk):
    """Activer/désactiver un code promo (POST uniquement)"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    promo_code = get_object_or_404(PromoCode, pk=pk, store=request.user.store)
    if request.method == 'POST':
        promo_code.is_active = not promo_code.is_active
        promo_code.save()
        status = 'activé' if promo_code.is_active else 'désactivé'
        messages.success(request, f'Code promo {status}.')
    return redirect('marketing:promo_codes')


# ========== CAMPAGNES ==========
@login_required
def campaigns_list(request):
    """Liste des campagnes"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    store = request.user.store
    campaigns = Campaign.objects.filter(store=store).order_by('-created_at')
    for camp in campaigns:
        camp.refresh_status()

    context = {
        'campaigns': campaigns,
        'active_count': sum(1 for c in campaigns if c.status == 'active'),
        'scheduled_count': sum(1 for c in campaigns if c.status == 'scheduled'),
        'completed_count': sum(1 for c in campaigns if c.status == 'completed'),
        'products': Product.objects.filter(store=store, is_active=True).order_by('name'),
        'promo_codes': PromoCode.objects.filter(store=store, is_active=True),
    }
    return render(request, 'marketing/campaigns_list.html', context)


def campaign_public(request, pk):
    """Page publique d'une campagne (vitrine). Compte le clic."""
    campaign = get_object_or_404(Campaign, pk=pk)
    campaign.refresh_status()
    if campaign.status != 'active':
        messages.info(request, 'Cette campagne est terminée.')
        return redirect('catalog:product_list')
    # Incrément atomique (deux visites simultanées ne s'écrasent plus) ; une seule fois par visiteur et session
    seen = request.session.get('campaigns_seen', [])
    if pk not in seen:
        Campaign.objects.filter(pk=pk).update(clicks_count=F('clicks_count') + 1, views_count=F('views_count') + 1)
        request.session['campaigns_seen'] = (seen + [pk])[-50:]
    products = campaign.target_products.filter(is_active=True)
    return render(request, 'marketing/campaign_public.html', {
        'campaign': campaign,
        'products': products,
    })


@login_required
def campaign_create(request):
    """Créer une campagne"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    store = request.user.store
    products = Product.objects.filter(store=store, is_active=True)
    promo_codes = PromoCode.objects.filter(store=store, is_active=True)

    if request.method == 'POST':
        campaign, errors, target_products, promo = _campaign_from_post(request, store)
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('marketing:campaigns')
        campaign.promo_code = promo
        campaign.save()
        campaign.target_products.set(target_products)
        campaign.refresh_status()
        messages.success(request, 'Campagne créée avec succès !')
        return redirect('marketing:campaigns')

    context = {
        'products': products,
        'promo_codes': promo_codes,
    }
    return render(request, 'marketing/campaign_form.html', context)


@login_required
def campaign_detail(request, pk):
    """Détail d'une campagne avec analytics"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    campaign = get_object_or_404(Campaign, pk=pk, store=request.user.store)

    context = {
        'campaign': campaign,
    }
    return render(request, 'marketing/campaign_detail.html', context)


@login_required
def campaign_edit(request, pk):
    """Modifier une campagne"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    campaign = get_object_or_404(Campaign, pk=pk, store=request.user.store)
    products = Product.objects.filter(store=request.user.store, is_active=True)
    promo_codes = PromoCode.objects.filter(store=request.user.store, is_active=True)

    if request.method == 'POST':
        campaign, errors, target_products, promo = _campaign_from_post(request, request.user.store, campaign)
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('marketing:campaign_edit', pk=pk)
        campaign.promo_code = promo
        campaign.save()
        campaign.target_products.set(target_products)
        campaign.refresh_status()
        messages.success(request, 'Campagne modifiée !')
        return redirect('marketing:campaigns')

    context = {
        'campaign': campaign,
        'products': products,
        'promo_codes': promo_codes,
    }
    return render(request, 'marketing/campaign_form.html', context)


# ========== ANALYTICS ==========
@login_required
def analytics(request):
    """Analytics marketing basés sur les données réelles (commandes, campagnes, emails, fidélité)"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    store = request.user.store
    now = timezone.now()
    last_30_days = now - timedelta(days=30)

    # --- Ventes de la boutique (30 derniers jours) ---
    # Uniquement SES lignes (une commande peut contenir d'autres vendeurs), payées, hors annulées/remboursées
    paid_items = _store_paid_items(store, last_30_days)
    line = F('price') * F('quantity')
    orders_count = paid_items.values('order').distinct().count()
    revenue = paid_items.aggregate(t=Sum(line))['t'] or 0
    avg_order = round(revenue / orders_count) if orders_count else 0

    # Ventes par jour (30 jours) pour le graphique
    from django.db.models.functions import TruncDate
    daily = (
        paid_items.annotate(day=TruncDate('order__created_at'))
        .values('day').annotate(total=Sum(line), count=Count('order', distinct=True))
        .order_by('day')
    )
    daily_map = {d['day']: d for d in daily}
    chart_labels, chart_revenue, chart_orders = [], [], []
    today = timezone.localdate()
    for i in range(30):
        day = today - timedelta(days=29 - i)
        chart_labels.append(day.strftime('%d/%m'))
        entry = daily_map.get(day)
        chart_revenue.append(float(entry['total']) if entry else 0)
        chart_orders.append(entry['count'] if entry else 0)

    # --- Campagnes ---
    campaigns = Campaign.objects.filter(store=store)
    for camp in campaigns:
        camp.refresh_status()
    campaigns_performance = campaigns.order_by('-revenue_generated')[:10]
    camp_totals = campaigns.aggregate(
        views=Sum('views_count'), clicks=Sum('clicks_count'),
        conversions=Sum('conversions_count'), revenue=Sum('revenue_generated'),
    )

    # --- Emails ---
    emails = Newsletter.objects.filter(store=store)
    emails_sent = emails.filter(status='sent').count()
    emails_recipients = emails.aggregate(t=Sum('recipients_count'))['t'] or 0

    # --- Messagerie ---
    messaging = MessagingCampaign.objects.filter(store=store)
    messaging_sent = sum(len(c.get_sent_list()) for c in messaging)

    # --- Fidélité ---
    loyalty_members = LoyaltyProgram.objects.filter(store=store).count()

    # --- Top produits (par quantité vendue, 30 jours) ---
    from orders.models import OrderItem
    top_products = (
        paid_items
        .values('product__name')
        .annotate(qty=Sum('quantity'), revenue=Sum(F('quantity') * F('price')))
        .order_by('-qty')[:5]
    )

    context = {
        'orders_count': orders_count,
        'revenue': revenue,
        'avg_order': avg_order,
        'chart': {'labels': chart_labels, 'revenue': chart_revenue, 'orders': chart_orders},
        'campaigns_performance': campaigns_performance,
        'camp_totals': camp_totals,
        'emails_sent': emails_sent,
        'emails_recipients': emails_recipients,
        'messaging_count': messaging.count(),
        'messaging_sent': messaging_sent,
        'loyalty_members': loyalty_members,
        'top_products': top_products,
    }
    return render(request, 'marketing/analytics.html', context)


# ========== PROGRAMME FIDÉLITÉ ==========
@login_required
def loyalty_program(request):
    """Gestion du programme de fidélité"""
    if not request.user.is_seller or not (request.user.store is not None):
        return redirect('dashboard:index')

    store = request.user.store

    # Sauvegarde des réglages du programme
    from .models import LoyaltySettings
    cfg, _ = LoyaltySettings.objects.get_or_create(store=store)
    if request.method == 'POST':
        values = {}
        for field in ['points_per_amount', 'silver_threshold', 'silver_rate',
                      'gold_threshold', 'gold_rate', 'platinum_threshold', 'platinum_rate']:
            val = str(request.POST.get(field, getattr(cfg, field))).strip()
            values[field] = int(val) if val.isdigit() else getattr(cfg, field)
        errors = []
        if values['points_per_amount'] < 1:
            errors.append('Il faut au moins 1 FCFA par point.')
        if not (0 < values['silver_threshold'] < values['gold_threshold'] < values['platinum_threshold']):
            errors.append('Les seuils doivent être croissants : Argent < Or < Platine.')
        if not (0 <= values['silver_rate'] <= values['gold_rate'] <= values['platinum_rate'] <= 50):
            errors.append('Les réductions doivent être croissantes et ne pas dépasser 50 %.')
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('marketing:loyalty')
        for field, val in values.items():
            setattr(cfg, field, val)
        cfg.save()
        # Recalculer les niveaux de tous les membres
        for m in LoyaltyProgram.objects.filter(store=store):
            m.update_tier()
            m.save()
        messages.success(request, 'Réglages du programme de fidélité enregistrés.')
        return redirect('marketing:loyalty')

    members = LoyaltyProgram.objects.filter(store=store).select_related('user').order_by('-points')

    # Statistiques
    stats = members.aggregate(
        total_members=Count('id'),
        total_points=Sum('points'),
        avg_points=Avg('points'),
        total_spent=Sum('total_spent'),
    )

    # Par niveau
    tier_breakdown = {t['tier']: t['count'] for t in members.values('tier').annotate(count=Count('id'))}

    context = {
        'members': members,
        'stats': stats,
        'cfg': cfg,
        'tier_bronze': tier_breakdown.get('bronze', 0),
        'tier_silver': tier_breakdown.get('silver', 0),
        'tier_gold': tier_breakdown.get('gold', 0),
        'tier_platinum': tier_breakdown.get('platinum', 0),
    }
    return render(request, 'marketing/loyalty_program.html', context)


# ========== EMAIL MARKETING ==========
def _get_seller_store(request):
    """Retourne la boutique du vendeur ou None."""
    if not request.user.is_seller:
        return None
    return getattr(request.user, 'store', None)


def _get_email_recipients(store, audience):
    """Retourne le queryset des destinataires selon l'audience (désinscrits exclus)."""
    from accounts.models import User
    if audience == 'customers':
        # Clients ayant commandé un produit de cette boutique
        users = User.objects.filter(orders__items__product__store=store)
    else:
        # 'all' : tous les acheteurs de la plateforme
        users = User.objects.filter(role='buyer')
    users = users.filter(email__isnull=False).exclude(email='')
    users = users.exclude(notification_prefs__email_marketing=False)
    return users.distinct()


def _get_newsletter_emails(newsletter):
    """Retourne la liste d'emails pour une newsletter (toutes audiences, désinscrits exclus)."""
    if newsletter.target_audience == 'custom':
        from accounts.models import User
        emails = [e.strip().lower() for e in newsletter.custom_recipients.splitlines() if e.strip()]
        opted_out = set(User.objects.filter(email__in=emails, notification_prefs__email_marketing=False)
                        .values_list('email', flat=True))
        return [e for e in emails if e not in {o.lower() for o in opted_out}]
    return list(_get_email_recipients(
        newsletter.store, newsletter.target_audience
    ).values_list('email', flat=True))


def _parse_recipients_excel(file):
    """Lit un fichier Excel et retourne la liste des emails valides (1ère colonne)."""
    import openpyxl
    import re
    emails = []
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    ws = wb.active
    email_re = re.compile(r'^[\w.\-+]+@[\w\-]+\.[\w.\-]+$')
    for row in ws.iter_rows(values_only=True):
        if not row or row[0] is None:
            continue
        val = str(row[0]).strip().lower()
        if val == 'email':  # en-tête
            continue
        if email_re.match(val) and val not in emails:
            emails.append(val)
    wb.close()
    return emails


def _build_email_html(newsletter, base_url=None, unsubscribe_url=None):
    """Construit le HTML de l'email avec branding boutique + produits + code promo.
    Les noms (produits, boutique) sont échappés ; le contenu est le HTML rédigé par le vendeur."""
    from django.utils.html import escape
    base_url = base_url or _site_url()
    store = newsletter.store

    # Bloc produits (2 colonnes)
    products_block = ''
    products = list(newsletter.products.all())
    if products:
        cells = []
        for p in products:
            img = f'<img src="{base_url}{p.image.url}" style="width:100%;height:140px;object-fit:cover;display:block;">' if p.image else '<div style="width:100%;height:140px;background:#f0f1f3;"></div>'
            old = f'<span style="text-decoration:line-through;color:#98a2b3;font-size:11px;margin-left:6px;">{p.old_price:.0f} FCFA</span>' if p.old_price else ''
            cells.append(f'''
                <td style="width:50%;padding:6px;vertical-align:top;">
                    <div style="border:1px solid #eaecf0;border-radius:8px;overflow:hidden;">
                        {img}
                        <div style="padding:12px;">
                            <div style="font-weight:700;font-size:13px;color:#101828;margin-bottom:4px;">{escape(p.name)}</div>
                            <div style="margin-bottom:8px;"><span style="color:#ff6a00;font-weight:800;font-size:15px;">{p.price:.0f} FCFA</span>{old}</div>
                            <a href="{base_url}{p.get_absolute_url()}" style="display:block;background:#ff6a00;color:#fff;text-align:center;padding:8px;border-radius:6px;text-decoration:none;font-size:12px;font-weight:700;">Voir le produit</a>
                        </div>
                    </div>
                </td>''')
        rows = ''
        for i in range(0, len(cells), 2):
            pair = cells[i] + (cells[i + 1] if i + 1 < len(cells) else '<td style="width:50%;"></td>')
            rows += f'<tr>{pair}</tr>'
        products_block = f'<table style="width:100%;border-collapse:collapse;margin-top:16px;">{rows}</table>'

    promo_block = ''
    if newsletter.promo_code:
        p = newsletter.promo_code
        if p.discount_type == 'percentage':
            reduction = f'-{p.discount_value:.0f}%'
        else:
            reduction = f'-{p.discount_value:.0f} FCFA'
        promo_block = f'''
        <div style="background:#fff4ec;border:2px dashed #ff6a00;border-radius:10px;padding:20px;text-align:center;margin:24px 0;">
            <div style="font-size:13px;color:#666;margin-bottom:6px;">Profitez de {reduction} avec le code</div>
            <div style="font-size:26px;font-weight:800;letter-spacing:3px;color:#ff6a00;">{p.code}</div>
            <div style="font-size:11px;color:#999;margin-top:6px;">Valable jusqu'au {p.valid_to.strftime('%d/%m/%Y')}</div>
        </div>'''
    return f'''<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#fff;">
    <div style="background:#ff6a00;padding:24px;text-align:center;">
        <div style="color:#fff;font-size:22px;font-weight:800;">{store.name}</div>
    </div>
    <div style="padding:32px 28px;color:#333;font-size:14px;line-height:1.7;">
        {newsletter.content}
        {products_block}
        {promo_block}
    </div>
    <div style="background:#f9fafb;padding:18px;text-align:center;font-size:11px;color:#98a2b3;">
        {escape(store.name)} · {escape(store.city or 'Douala')} · Comptoir
        {f'<br><a href="{unsubscribe_url}" style="color:#98a2b3;">Se désinscrire des emails promotionnels</a>' if unsubscribe_url else ''}
    </div>
</div>
</body></html>'''


def _unsubscribe_url(email):
    """Lien de désinscription signé (pas besoin d'être connecté pour l'utiliser)."""
    from django.core import signing
    from django.urls import reverse
    token = signing.dumps(email.lower(), salt='marketing-unsubscribe')
    return _site_url() + reverse('marketing:unsubscribe', args=[token])


def unsubscribe(request, token):
    """Désinscription des emails promotionnels depuis le lien de l'email."""
    from django.core import signing
    from accounts.models import User
    from messaging.models import NotificationPreference
    try:
        email = signing.loads(token, salt='marketing-unsubscribe')
    except signing.BadSignature:
        return render(request, 'marketing/unsubscribe.html', {'ok': False}, status=400)
    for user in User.objects.filter(email__iexact=email):
        prefs, _ = NotificationPreference.objects.get_or_create(user=user)
        prefs.email_marketing = False
        prefs.save(update_fields=['email_marketing'])
    return render(request, 'marketing/unsubscribe.html', {'ok': True, 'email': email})


@login_required
def emails_list(request):
    """Liste des campagnes email"""
    store = _get_seller_store(request)
    if not store:
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')

    emails = Newsletter.objects.filter(store=store).select_related('promo_code')
    return render(request, 'marketing/emails_list.html', {
        'emails': emails,
        'sent_count': emails.filter(status='sent').count(),
        'draft_count': emails.filter(status='draft').count(),
        'customers_count': _get_email_recipients(store, 'customers').count(),
        'all_count': _get_email_recipients(store, 'all').count(),
    })


@login_required
def email_create(request):
    """Créer (et optionnellement envoyer) une campagne email"""
    store = _get_seller_store(request)
    if not store:
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')

    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        content = request.POST.get('content', '').strip()
        audience = request.POST.get('target_audience', 'customers')
        promo_id = request.POST.get('promo_code') or None
        action = request.POST.get('action', 'draft')

        if not subject or not content:
            messages.error(request, 'Le sujet et le contenu sont requis.')
            return redirect('marketing:email_create')

        # Audience personnalisée via fichier Excel
        custom_emails = []
        if audience == 'custom':
            excel_file = request.FILES.get('recipients_file')
            if not excel_file:
                messages.error(request, 'Veuillez téléverser le fichier Excel des destinataires.')
                return redirect('marketing:email_create')
            try:
                custom_emails = _parse_recipients_excel(excel_file)
            except Exception:
                messages.error(request, 'Fichier Excel illisible. Utilisez le template fourni.')
                return redirect('marketing:email_create')
            if not custom_emails:
                messages.error(request, 'Aucun email valide trouvé dans le fichier.')
                return redirect('marketing:email_create')

        if audience not in ('customers', 'all', 'custom'):
            audience = 'customers'
        newsletter = Newsletter.objects.create(
            store=store,
            subject=subject[:200],
            content=content,
            target_audience=audience,
            promo_code=_store_promo(store, promo_id),
            custom_recipients='\n'.join(custom_emails),
            status='draft',
        )
        newsletter.products.set(_store_products(store, request.POST.getlist('products')))

        if action == 'send':
            return _send_newsletter(request, newsletter)

        if action == 'schedule':
            scheduled_at = request.POST.get('scheduled_at', '').strip()
            if not scheduled_at:
                messages.error(request, 'Veuillez choisir la date et l\'heure d\'envoi.')
                return redirect('marketing:email_create')
            from datetime import datetime
            try:
                naive_dt = datetime.strptime(scheduled_at, '%Y-%m-%dT%H:%M')
                newsletter.scheduled_at = timezone.make_aware(naive_dt)
            except ValueError:
                messages.error(request, 'Date de programmation invalide.')
                return redirect('marketing:email_create')
            if newsletter.scheduled_at <= timezone.now():
                messages.error(request, 'La date d\'envoi doit être dans le futur.')
                return redirect('marketing:email_create')
            newsletter.status = 'scheduled'
            newsletter.save()
            messages.success(request, f'Campagne programmée pour le {newsletter.scheduled_at.strftime("%d/%m/%Y à %H:%M")}.')
            return redirect('marketing:emails_list')

        messages.success(request, 'Campagne email enregistrée en brouillon.')
        return redirect('marketing:emails_list')

    return render(request, 'marketing/email_form.html', {
        'promo_codes': PromoCode.objects.filter(store=store, is_active=True),
        'products': Product.objects.filter(store=store, is_active=True).order_by('name'),
        'customers_count': _get_email_recipients(store, 'customers').count(),
        'all_count': _get_email_recipients(store, 'all').count(),
    })


def _send_newsletter_now(newsletter):
    """Envoie une newsletter à son audience. Retourne le nombre d'envois."""
    from django.core.mail import EmailMessage
    from django.conf import settings as dj_settings

    recipients = _get_newsletter_emails(newsletter)
    if not recipients:
        return 0

    sent = 0
    for email in recipients:
        try:
            # Un lien de désinscription personnel par destinataire
            html = _build_email_html(newsletter, unsubscribe_url=_unsubscribe_url(email))
            msg = EmailMessage(
                subject=newsletter.subject,
                body=html,
                from_email=dj_settings.DEFAULT_FROM_EMAIL,
                to=[email],
            )
            msg.content_subtype = 'html'
            msg.send(fail_silently=True)
            sent += 1
        except Exception:
            pass

    newsletter.status = 'sent'
    newsletter.sent_at = timezone.now()
    newsletter.recipients_count = sent
    newsletter.save()
    return sent


def _send_newsletter(request, newsletter):
    """Envoie une newsletter (contexte web avec messages)."""
    sent = _send_newsletter_now(newsletter)
    if sent == 0:
        messages.warning(request, "Aucun destinataire pour cette audience.")
    else:
        messages.success(request, f'Campagne envoyée à {sent} destinataire{"s" if sent > 1 else ""}.')
    return redirect('marketing:emails_list')


@login_required
def email_send(request, pk):
    """Envoyer un brouillon existant"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    newsletter = get_object_or_404(Newsletter, pk=pk, store=store)
    if request.method == 'POST':
        if newsletter.status == 'sent':
            messages.warning(request, 'Cette campagne a déjà été envoyée.')
            return redirect('marketing:emails_list')
        return _send_newsletter(request, newsletter)
    return redirect('marketing:emails_list')


@login_required
def email_recipients_template(request):
    """Télécharge le template Excel pour les destinataires personnalisés."""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    import openpyxl
    from django.http import HttpResponse
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Destinataires'
    ws.append(['email'])
    ws.append(['client1@example.com'])
    ws.append(['client2@example.com'])
    ws.append(['client3@example.com'])
    ws.column_dimensions['A'].width = 35
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="template_destinataires.xlsx"'
    wb.save(response)
    return response


@login_required
def email_detail(request, pk):
    """Aperçu d'une campagne email"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    newsletter = get_object_or_404(Newsletter, pk=pk, store=store)
    return render(request, 'marketing/email_detail.html', {
        'email': newsletter,
        'html_preview': _build_email_html(newsletter),
    })


@login_required
def email_delete(request, pk):
    """Supprimer un brouillon"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    newsletter = get_object_or_404(Newsletter, pk=pk, store=store)
    if request.method == 'POST':
        newsletter.delete()
        messages.success(request, 'Campagne supprimée.')
    return redirect('marketing:emails_list')


# ========== WHATSAPP / TELEGRAM ==========
from .models import MessagingCampaign


def _parse_numbers_excel(file):
    """Lit un Excel et retourne les numéros de la 1ère colonne."""
    import openpyxl
    numbers = []
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    ws = wb.active
    for row in ws.iter_rows(values_only=True):
        if not row or row[0] is None:
            continue
        val = str(row[0]).strip()
        if val.lower() in ('numero', 'numéro', 'telephone', 'téléphone', 'phone', 'number'):
            continue
        if val:
            numbers.append(val)
    wb.close()
    return numbers


@login_required
def messaging_list(request):
    """Liste des campagnes WhatsApp/Telegram"""
    store = _get_seller_store(request)
    if not store:
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')
    campaigns = MessagingCampaign.objects.filter(store=store)
    return render(request, 'marketing/messaging_list.html', {
        'campaigns': campaigns,
    })


@login_required
def messaging_create(request):
    """Créer une campagne WhatsApp/Telegram"""
    store = _get_seller_store(request)
    if not store:
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        message = request.POST.get('message', '').strip()
        channel = request.POST.get('channel', 'whatsapp')
        audience = request.POST.get('audience', 'customers')
        promo_id = request.POST.get('promo_code') or None

        if not name or not message:
            messages.error(request, 'Le nom et le message sont requis.')
            return redirect('marketing:messaging_create')

        custom_numbers = ''
        if audience == 'custom':
            excel_file = request.FILES.get('numbers_file')
            if not excel_file:
                messages.error(request, 'Veuillez téléverser le fichier Excel des numéros.')
                return redirect('marketing:messaging_create')
            try:
                numbers = _parse_numbers_excel(excel_file)
            except Exception:
                messages.error(request, 'Fichier Excel illisible. Utilisez le template fourni.')
                return redirect('marketing:messaging_create')
            if not numbers:
                messages.error(request, 'Aucun numéro trouvé dans le fichier.')
                return redirect('marketing:messaging_create')
            custom_numbers = '\n'.join(numbers)

        if channel not in dict(MessagingCampaign.CHANNEL_CHOICES):
            channel = 'whatsapp'
        if audience not in dict(MessagingCampaign.AUDIENCE_CHOICES):
            audience = 'customers'
        campaign = MessagingCampaign.objects.create(
            store=store, name=name[:200], message=message, channel=channel,
            audience=audience, promo_code=_store_promo(store, promo_id), custom_numbers=custom_numbers,
        )
        campaign.products.set(_store_products(store, request.POST.getlist('products')))

        messages.success(request, f'Campagne « {name} » créée.')
        return redirect('marketing:messaging_detail', pk=campaign.pk)

    return render(request, 'marketing/messaging_form.html', {
        'promo_codes': PromoCode.objects.filter(store=store, is_active=True),
        'products': Product.objects.filter(store=store, is_active=True).order_by('name'),
    })


@login_required
def messaging_detail(request, pk):
    """Console d'envoi d'une campagne WhatsApp/Telegram"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    campaign = get_object_or_404(MessagingCampaign, pk=pk, store=store)
    recipients = campaign.get_recipients()
    sent = set(campaign.get_sent_list())
    base_url = request.build_absolute_uri('/')[:-1]
    full_message = campaign.build_message(base_url)

    from urllib.parse import quote
    rows = []
    for number in recipients:
        if campaign.channel == 'whatsapp':
            url = f'https://wa.me/{number}?text={quote(full_message)}'
        else:
            url = f'https://t.me/share/url?url={quote(base_url)}&text={quote(full_message)}'
        rows.append({'number': number, 'url': url, 'sent': number in sent})

    return render(request, 'marketing/messaging_detail.html', {
        'campaign': campaign,
        'rows': rows,
        'full_message': full_message,
        'total': len(rows),
        'sent_count': len(sent),
    })


@login_required
def messaging_mark_sent(request, pk):
    """Marque un numéro comme envoyé (appel AJAX)."""
    from django.http import JsonResponse
    store = _get_seller_store(request)
    if not store:
        return JsonResponse({'ok': False}, status=403)
    campaign = get_object_or_404(MessagingCampaign, pk=pk, store=store)
    if request.method == 'POST':
        number = request.POST.get('number', '').strip()
        sent = campaign.get_sent_list()
        if number not in campaign.get_recipients():
            return JsonResponse({'ok': False, 'error': 'Numéro absent de la campagne'}, status=400)
        if number not in sent:
            sent.append(number)
            campaign.sent_numbers = '\n'.join(sent)
            if len(sent) >= len(campaign.get_recipients()):
                campaign.status = 'done'
            elif campaign.status == 'draft':
                campaign.status = 'in_progress'
            campaign.save()
        return JsonResponse({'ok': True, 'sent_count': len(sent), 'progress': campaign.progress})
    return JsonResponse({'ok': False}, status=405)


@login_required
def messaging_delete(request, pk):
    """Supprimer une campagne"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    campaign = get_object_or_404(MessagingCampaign, pk=pk, store=store)
    if request.method == 'POST':
        campaign.delete()
        messages.success(request, 'Campagne supprimée.')
    return redirect('marketing:messaging_list')


@login_required
def messaging_numbers_template(request):
    """Template Excel pour l'import de numéros."""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    import openpyxl
    from django.http import HttpResponse
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Numéros'
    ws.append(['numero'])
    ws.append(['237690000001'])
    ws.append(['237690000002'])
    ws.append(['+237699000003'])
    ws.column_dimensions['A'].width = 25
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="template_numeros.xlsx"'
    wb.save(response)
    return response


# ========== FACEBOOK ==========
from urllib.parse import quote
from .models import FacebookPost


@login_required
def facebook_list(request):
    """Liste des posts Facebook"""
    store = _get_seller_store(request)
    if not store:
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')
    posts = FacebookPost.objects.filter(store=store)
    return render(request, 'marketing/facebook_list.html', {
        'posts': posts,
        'published_count': posts.filter(status='published').count(),
    })


@login_required
def facebook_create(request):
    """Studio de création de post Facebook"""
    store = _get_seller_store(request)
    if not store:
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        hashtags = request.POST.get('hashtags', '').strip()
        promo_id = request.POST.get('promo_code') or None

        if not title or not content:
            messages.error(request, 'Le titre et le contenu sont requis.')
            return redirect('marketing:facebook_create')

        post = FacebookPost.objects.create(
            store=store, title=title[:200], content=content,
            hashtags=hashtags[:300], promo_code=_store_promo(store, promo_id),
        )
        post.products.set(_store_products(store, request.POST.getlist('products')))

        messages.success(request, f'Post « {title} » créé.')
        return redirect('marketing:facebook_detail', pk=post.pk)

    return render(request, 'marketing/facebook_form.html', {
        'products': Product.objects.filter(store=store, is_active=True).order_by('name'),
        'promo_codes': PromoCode.objects.filter(store=store, is_active=True),
    })


@login_required
def facebook_detail(request, pk):
    """Aperçu du post + publication"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    post = get_object_or_404(FacebookPost, pk=pk, store=store)
    base_url = request.build_absolute_uri('/')[:-1]
    post_text = post.build_post_text(base_url)

    if request.method == 'POST':
        post.status = 'published'
        post.published_at = timezone.now()
        post.save()
        messages.success(request, 'Post marqué comme publié.')
        return redirect('marketing:facebook_list')

    # Lien vers la page Facebook de la boutique (configurée dans les paramètres)
    fb_page_url = store.facebook or ''

    return render(request, 'marketing/facebook_detail.html', {
        'post': post,
        'post_text': post_text,
        'fb_page_url': fb_page_url,
        # Partage : la boutique (ou le premier produit du post) plutôt que la page d'accueil du site
        'share_url': 'https://www.facebook.com/sharer/sharer.php?u=' + quote(_post_share_target(post, base_url), safe=''),
    })


def _post_share_target(post, base_url):
    first = post.products.first()
    if first:
        return base_url + first.get_absolute_url()
    from django.urls import reverse
    return base_url + reverse('store:detail', args=[post.store.slug])


@login_required
def facebook_delete(request, pk):
    """Supprimer un post"""
    store = _get_seller_store(request)
    if not store:
        return redirect('dashboard:index')
    post = get_object_or_404(FacebookPost, pk=pk, store=store)
    if request.method == 'POST':
        post.delete()
        messages.success(request, 'Post supprimé.')
    return redirect('marketing:facebook_list')
