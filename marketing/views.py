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

    context = {
        'promo_codes': promo_codes,
        'active_count': promo_codes.filter(is_active=True).count(),
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
    """Activer/désactiver un code promo"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    promo_code = get_object_or_404(PromoCode, pk=pk, store=request.user.store)
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

    context = {
        'campaigns': campaigns,
        'active_count': campaigns.filter(status='active').count(),
    }
    return render(request, 'marketing/campaigns_list.html', context)


@login_required
def campaign_create(request):
    """Créer une campagne"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    products = Product.objects.filter(store=store, is_active=True)
    promo_codes = PromoCode.objects.filter(store=store, is_active=True)

    if request.method == 'POST':
        campaign = Campaign.objects.create(
            store=store,
            name=request.POST.get('name'),
            campaign_type=request.POST.get('campaign_type'),
            description=request.POST.get('description'),
            start_date=request.POST.get('start_date'),
            end_date=request.POST.get('end_date'),
            status=request.POST.get('status', 'draft'),
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
        campaign.name = request.POST.get('name')
        campaign.campaign_type = request.POST.get('campaign_type')
        campaign.description = request.POST.get('description')
        campaign.start_date = request.POST.get('start_date')
        campaign.end_date = request.POST.get('end_date')
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
    """Analytics détaillés"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    now = timezone.now()
    last_30_days = now - timedelta(days=30)

    # Analytics quotidiens
    daily_analytics = MarketingAnalytics.objects.filter(
        store=store, date__gte=last_30_days.date()
    ).order_by('date')

    # Statistiques globales
    stats = daily_analytics.aggregate(
        total_views=Sum('page_views'),
        total_visitors=Sum('unique_visitors'),
        total_purchases=Sum('purchases'),
        total_revenue=Sum('revenue'),
        avg_order_value=Avg('avg_order_value'),
    )

    # Performance des campagnes
    campaigns_performance = Campaign.objects.filter(
        store=store, status='active'
    ).annotate(
        roi_calc=F('revenue_generated') - F('budget')
    ).order_by('-revenue_generated')[:10]

    context = {
        'daily_analytics': daily_analytics,
        'stats': stats,
        'campaigns_performance': campaigns_performance,
    }
    return render(request, 'marketing/analytics.html', context)


# ========== PROGRAMME FIDÉLITÉ ==========
@login_required
def loyalty_program(request):
    """Gestion du programme de fidélité"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    members = LoyaltyProgram.objects.filter(store=store).select_related('user').order_by('-points')

    # Statistiques
    stats = members.aggregate(
        total_members=Count('id'),
        total_points=Sum('points'),
        avg_points=Avg('points'),
        total_spent=Sum('total_spent'),
    )

    # Par niveau
    tier_breakdown = members.values('tier').annotate(count=Count('id'))

    context = {
        'members': members,
        'stats': stats,
        'tier_breakdown': tier_breakdown,
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
