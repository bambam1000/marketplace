from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from .models import SubscriptionPlan, Subscription, SubscriptionPayment, PaymentConfig, ProductBoost
from catalog.models import Product


def plans_view(request):
    from .limits import comparison, highlights
    plans = list(SubscriptionPlan.objects.filter(is_active=True, is_trial=False))
    for plan in plans:
        plan.highlights = highlights(plan)
    current_sub = pending = None
    if request.user.is_authenticated:
        current_sub = Subscription.objects.filter(user=request.user).select_related('plan').first()
        if current_sub:
            pending = current_sub.payments.filter(status='pending').select_related('plan').first()
    in_dash = request.user.is_authenticated and request.user.store is not None
    return render(request, 'billing/plans.html', {
        'layout': 'dashboard/base.html' if in_dash else 'base.html',
        'plans': plans,
        'comparison': comparison(plans),
        'current_sub': current_sub,
        'pending': pending,
    })


@login_required
def subscribe_view(request, plan_slug):
    """Demande d'abonnement : la formule n'est activée qu'après validation du paiement par Comptoir."""
    plan = get_object_or_404(SubscriptionPlan, slug=plan_slug, is_active=True, is_trial=False)
    if request.user.store is None:
        messages.error(request, 'Créez votre boutique pour choisir une formule.')
        return redirect('accounts:register_seller')
    if request.method == 'POST':
        from .subscriptions import request_plan
        method = request.POST.get('payment_method', 'momo')
        if method not in dict(SubscriptionPayment._meta.get_field('payment_method').choices):
            method = 'momo'
        phone = request.POST.get('payer_phone', '').strip()
        if method in ('momo', 'om') and len(''.join(c for c in phone if c.isdigit())) < 8:
            messages.error(request, 'Indiquez le numéro Mobile Money depuis lequel vous payez.')
            return redirect('billing:subscribe', plan_slug=plan.slug)
        payment = request_plan(request.user, plan, request.POST.get('billing_cycle', 'monthly'), method, phone)
        messages.success(request, f'Demande enregistrée : la formule {plan.name} sera activée dès réception de votre paiement '
                                  f'de {int(payment.amount):,} F.'.replace(',', ' '))
        return redirect('billing:my_subscription')
    from .models import SubscriptionSettings
    return render(request, 'billing/subscribe.html', {
        'plan': plan, 'pay': SubscriptionSettings.get(),
        'fallback_instructions': getattr(settings, 'SUBSCRIPTION_PAYMENT_INSTRUCTIONS', ''),
    })


@login_required
def my_subscription(request):
    from .limits import subscription_of, usage
    store = request.user.store
    sub = subscription_of(store) if store else Subscription.objects.filter(user=request.user).first()
    payments = SubscriptionPayment.objects.filter(subscription=sub).select_related('plan').order_by('-created_at')[:10] if sub else []
    return render(request, 'billing/my_subscription.html', {
        'subscription': sub, 'payments': payments,
        'pending': next((p for p in payments if p.status == 'pending'), None),
        'usage': usage(store) if store else [],
    })


@login_required
def payment_config(request):
    """Ancienne adresse : les moyens de paiement se règlent dans Paramètres > Paiements."""
    from django.urls import reverse
    from .payments import save_config
    if request.method == 'POST':
        config, _ = PaymentConfig.objects.get_or_create(user=request.user)
        errors = save_config(config, request.POST)
        for e in errors:
            messages.error(request, e)
        if not errors:
            messages.success(request, 'Moyens de paiement enregistrés.')
    return redirect(reverse('dashboard:settings') + '?tab=payments')


@login_required
def boost_product(request, product_id):
    product = get_object_or_404(Product, pk=product_id, store__owner=request.user)
    from .limits import blocked
    reason = blocked(product.store, feature='boost', user=request.user)
    if reason:
        messages.error(request, reason)
        return redirect('dashboard:products')
    if request.method == 'POST':
        platform = request.POST.get('platform', 'internal')
        budget_select = request.POST.get('budget')
        budget = int(request.POST.get('custom_budget', budget_select)) if budget_select == 'custom' else int(budget_select or 5000)
        duration = int(request.POST.get('duration', 7))

        # Collect targeting data
        targeting_data = {
            'city': request.POST.get('target_city', ''),
            'age': request.POST.get('target_age', ''),
            'fb_page_id': request.POST.get('fb_page_id', ''),
            'whatsapp_number': request.POST.get('whatsapp_number', ''),
        }

        # Calculate commission for external platforms
        commission_rate = getattr(settings, 'BOOST_COMMISSION_RATE', 0.20)
        client_budget = budget
        commission = 0
        platform_budget = budget

        if platform in ['facebook', 'whatsapp']:
            commission = int(client_budget * commission_rate)
            platform_budget = client_budget - commission

        boost = ProductBoost.objects.create(
            product=product,
            user=request.user,
            platform=platform,
            budget=budget,
            client_budget=client_budget,
            platform_budget=platform_budget,
            commission=commission,
            duration_days=duration,
            status='pending',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=duration),
            targeting_data=targeting_data if platform in ['facebook', 'whatsapp'] else None,
        )

        # Create transaction for client payment with commission tracking
        from accounting.models import Transaction
        Transaction.objects.create(
            user=request.user,
            type='boost',
            amount=client_budget,
            commission_amount=commission,
            net_amount=platform_budget,
            status='completed',
            reference=f"Boost {product.name[:30]} - {boost.get_platform_display()} ({duration}j)",
        )

        # Handle different platforms
        if boost.platform == 'internal':
            product.is_featured = True
            product.save(update_fields=['is_featured'])
            boost.status = 'active'
            boost.save(update_fields=['status'])

        elif boost.platform == 'facebook':
            # TODO: Integrate with Facebook Marketing API
            # For now, we'll create a task for manual processing
            messages.info(request, f'Votre campagne Facebook Ads sera activée sous 24h. Budget Facebook: {platform_budget:,} FCFA (Commission Comptoir: {commission:,} FCFA).')
            boost.status = 'pending'
            boost.save()

        elif boost.platform == 'whatsapp':
            # TODO: Integrate with WhatsApp Business API
            messages.info(request, f'Votre campagne WhatsApp sera activée sous 24h. Budget WhatsApp: {platform_budget:,} FCFA (Commission Comptoir: {commission:,} FCFA).')
            boost.status = 'pending'
            boost.save()

        success_msg = f'✅ Campagne créée ! Budget: {budget:,} FCFA · Durée: {duration} jours · Plateforme: {boost.get_platform_display()}'
        messages.success(request, success_msg)
        return redirect('billing:my_boosts')

    return render(request, 'billing/boost_product.html', {'product': product})


@login_required
def my_boosts(request):
    boosts = ProductBoost.objects.filter(user=request.user).select_related('product')
    return render(request, 'billing/my_boosts.html', {'boosts': boosts})
