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
