from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Avg, F
from datetime import timedelta
from .models import PromoCode, Campaign, Newsletter, MarketingAnalytics, LoyaltyProgram
from catalog.models import Product
from orders.models import Order


@login_required
def marketing_dashboard(request):
    """Dashboard marketing principal"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
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

    # Analytics des 30 derniers jours
    analytics = MarketingAnalytics.objects.filter(
        store=store, date__gte=last_30_days.date()
    ).aggregate(
        total_views=Sum('page_views'),
        total_visitors=Sum('unique_visitors'),
        total_purchases=Sum('purchases'),
        total_revenue=Sum('revenue')
    )

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
        'analytics': analytics,
        'recent_campaigns': recent_campaigns,
        'active_promo_codes': active_promo_codes,
    }
    return render(request, 'marketing/dashboard.html', context)


# ========== CODES PROMO ==========
@login_required
def promo_codes_list(request):
    """Liste des codes promo"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store

    if request.method == 'POST':
        PromoCode.objects.create(
            store=store,
            code=request.POST.get('code').upper(),
            description=request.POST.get('description', ''),
            discount_type=request.POST.get('discount_type', 'percentage'),
            discount_value=request.POST.get('discount_value'),
            min_purchase_amount=request.POST.get('min_purchase_amount', 0),
            max_discount_amount=request.POST.get('max_discount_amount') or None,
            usage_limit=request.POST.get('usage_limit') or None,
            valid_from=request.POST.get('valid_from'),
            valid_to=request.POST.get('valid_to'),
            is_active='is_active' in request.POST,
        )
        messages.success(request, 'Code promo créé avec succès !')
        return redirect('marketing:promo_codes')

    return render(request, 'marketing/promo_code_form.html')


@login_required
def promo_code_edit(request, pk):
    """Modifier un code promo"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    promo_code = get_object_or_404(PromoCode, pk=pk, store=request.user.store)

    if request.method == 'POST':
        promo_code.code = request.POST.get('code').upper()
        promo_code.description = request.POST.get('description', '')
        promo_code.discount_type = request.POST.get('discount_type', 'percentage')
        promo_code.discount_value = request.POST.get('discount_value')
        promo_code.min_purchase_amount = request.POST.get('min_purchase_amount', 0)
        promo_code.max_discount_amount = request.POST.get('max_discount_amount') or None
        promo_code.usage_limit = request.POST.get('usage_limit') or None
        promo_code.valid_from = request.POST.get('valid_from')
        promo_code.valid_to = request.POST.get('valid_to')
        promo_code.is_active = 'is_active' in request.POST
        promo_code.save()
        messages.success(request, 'Code promo modifié !')
        return redirect('marketing:promo_codes')

    return render(request, 'marketing/promo_code_form.html', {'promo_code': promo_code})


@login_required
def promo_code_toggle(request, pk):
    """Activer/désactiver un code promo (POST uniquement)"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
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
    Campaign.objects.filter(pk=pk).update(clicks_count=campaign.clicks_count + 1)
    products = campaign.target_products.filter(is_active=True)
    return render(request, 'marketing/campaign_public.html', {
        'campaign': campaign,
        'products': products,
    })


@login_required
def campaign_create(request):
    """Créer une campagne"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    products = Product.objects.filter(store=store, is_active=True)
    promo_codes = PromoCode.objects.filter(store=store, is_active=True)

    if request.method == 'POST':
        from datetime import datetime
        def parse_dt(val):
            try:
                return timezone.make_aware(datetime.strptime(val, '%Y-%m-%dT%H:%M'))
            except (ValueError, TypeError):
                return None
        start = parse_dt(request.POST.get('start_date'))
        end = parse_dt(request.POST.get('end_date'))
        if not start or not end or end <= start:
            messages.error(request, 'Dates de campagne invalides.')
            return redirect('marketing:campaigns')
        campaign = Campaign.objects.create(
            store=store,
            name=request.POST.get('name'),
            campaign_type=request.POST.get('campaign_type'),
            description=request.POST.get('description'),
            start_date=start,
            end_date=end,
            status=request.POST.get('status', 'scheduled'),
            budget=request.POST.get('budget', 0),
        )

        if request.FILES.get('banner'):
            campaign.banner = request.FILES['banner']
            campaign.save()

        # Ajouter les produits ciblés
        product_ids = request.POST.getlist('target_products')
        if product_ids:
            campaign.target_products.set(product_ids)

        # Associer un code promo
        promo_id = request.POST.get('promo_code')
        if promo_id:
            campaign.promo_code_id = promo_id
            campaign.save()

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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    campaign = get_object_or_404(Campaign, pk=pk, store=request.user.store)

    context = {
        'campaign': campaign,
    }
    return render(request, 'marketing/campaign_detail.html', context)


@login_required
def campaign_edit(request, pk):
    """Modifier une campagne"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    campaign = get_object_or_404(Campaign, pk=pk, store=request.user.store)
    products = Product.objects.filter(store=request.user.store, is_active=True)
    promo_codes = PromoCode.objects.filter(store=request.user.store, is_active=True)

    if request.method == 'POST':
        from datetime import datetime
        def parse_dt(val):
            try:
                return timezone.make_aware(datetime.strptime(val, '%Y-%m-%dT%H:%M'))
            except (ValueError, TypeError):
                return None
        start = parse_dt(request.POST.get('start_date'))
        end = parse_dt(request.POST.get('end_date'))
        if not start or not end or end <= start:
            messages.error(request, 'Dates de campagne invalides.')
            return redirect('marketing:campaign_edit', pk=pk)
        campaign.name = request.POST.get('name')
        campaign.campaign_type = request.POST.get('campaign_type')
        campaign.description = request.POST.get('description')
        campaign.start_date = start
        campaign.end_date = end
        campaign.status = request.POST.get('status', 'draft')
        campaign.budget = request.POST.get('budget', 0)

        if request.FILES.get('banner'):
            campaign.banner = request.FILES['banner']

        campaign.save()

        # Mettre à jour les produits
        product_ids = request.POST.getlist('target_products')
        campaign.target_products.set(product_ids)

        # Code promo
        promo_id = request.POST.get('promo_code')
        campaign.promo_code_id = promo_id if promo_id else None
        campaign.save()

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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    now = timezone.now()
    last_30_days = now - timedelta(days=30)

    # --- Commandes réelles de la boutique (30 derniers jours) ---
    orders_qs = Order.objects.filter(items__product__store=store, created_at__gte=last_30_days).distinct()
    orders_count = orders_qs.count()
    revenue = orders_qs.aggregate(t=Sum('total_amount'))['t'] or 0
    avg_order = round(revenue / orders_count) if orders_count else 0

    # Ventes par jour (30 jours) pour le graphique
    from django.db.models.functions import TruncDate
    daily = (
        orders_qs.annotate(day=TruncDate('created_at'))
        .values('day').annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('day')
    )
    daily_map = {d['day']: d for d in daily}
    chart_labels, chart_revenue, chart_orders = [], [], []
    for i in range(30):
        day = (last_30_days + timedelta(days=i + 1)).date()
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
        OrderItem.objects.filter(product__store=store, order__created_at__gte=last_30_days)
        .values('product__name')
        .annotate(qty=Sum('quantity'), revenue=Sum(F('quantity') * F('price')))
        .order_by('-qty')[:5]
    )

    context = {
        'orders_count': orders_count,
        'revenue': revenue,
        'avg_order': avg_order,
        'chart_labels': chart_labels,
        'chart_revenue': chart_revenue,
        'chart_orders': chart_orders,
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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store

    # Sauvegarde des réglages du programme
    from .models import LoyaltySettings
    cfg, _ = LoyaltySettings.objects.get_or_create(store=store)
    if request.method == 'POST':
        for field in ['points_per_amount', 'silver_threshold', 'silver_rate',
                      'gold_threshold', 'gold_rate', 'platinum_threshold', 'platinum_rate']:
            val = request.POST.get(field)
            if val is not None and str(val).isdigit():
                setattr(cfg, field, int(val))
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
    """Retourne le queryset des destinataires selon l'audience."""
    from accounts.models import User
    if audience == 'customers':
        # Clients ayant commandé un produit de cette boutique
        return User.objects.filter(
            orders__items__product__store=store, email__isnull=False
        ).exclude(email='').distinct()
    # 'all' : tous les acheteurs de la plateforme
    return User.objects.filter(
        role='buyer', email__isnull=False
    ).exclude(email='').distinct()


def _get_newsletter_emails(newsletter):
    """Retourne la liste d'emails pour une newsletter (toutes audiences)."""
    if newsletter.target_audience == 'custom':
        return [e.strip() for e in newsletter.custom_recipients.splitlines() if e.strip()]
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


def _build_email_html(newsletter, base_url='http://127.0.0.1:8000'):
    """Construit le HTML de l'email avec branding boutique + produits + code promo."""
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
                            <div style="font-weight:700;font-size:13px;color:#101828;margin-bottom:4px;">{p.name}</div>
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
        {store.name} · {store.city or 'Douala'} · AfriMarket
    </div>
</div>
</body></html>'''


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

        newsletter = Newsletter.objects.create(
            store=store,
            subject=subject,
            content=content,
            target_audience=audience,
            promo_code_id=promo_id,
            custom_recipients='\n'.join(custom_emails),
            status='draft',
        )
        product_ids = request.POST.getlist('products')
        if product_ids:
            newsletter.products.set(product_ids)

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

    html = _build_email_html(newsletter)
    sent = 0
    for email in recipients:
        try:
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

        campaign = MessagingCampaign.objects.create(
            store=store, name=name, message=message, channel=channel,
            audience=audience, promo_code_id=promo_id, custom_numbers=custom_numbers,
        )
        product_ids = request.POST.getlist('products')
        if product_ids:
            campaign.products.set(product_ids)

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
        if number and number not in sent:
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
