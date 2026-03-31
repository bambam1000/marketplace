from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from .models import SubscriptionPlan, Subscription, SubscriptionPayment, PaymentConfig, ProductBoost
from catalog.models import Product


def plans_view(request):
    plans = SubscriptionPlan.objects.filter(is_active=True)
    current_sub = None
    if request.user.is_authenticated:
        current_sub = Subscription.objects.filter(user=request.user).first()
    return render(request, 'billing/plans.html', {
        'plans': plans,
        'current_sub': current_sub,
    })


@login_required
def subscribe_view(request, plan_slug):
    plan = get_object_or_404(SubscriptionPlan, slug=plan_slug, is_active=True)
    if request.method == 'POST':
        cycle = request.POST.get('billing_cycle', 'monthly')
        amount = plan.price_yearly if cycle == 'yearly' and plan.price_yearly else plan.price_monthly
        duration = 365 if cycle == 'yearly' else 30

        sub, created = Subscription.objects.update_or_create(
            user=request.user,
            defaults={
                'plan': plan,
                'status': 'active',
                'billing_cycle': cycle,
                'end_date': timezone.now() + timedelta(days=duration),
                'boost_credits_remaining': plan.boost_credits_monthly,
            }
        )
        SubscriptionPayment.objects.create(
            subscription=sub,
            amount=amount,
            payment_method=request.POST.get('payment_method', 'momo'),
            status='completed',
        )
        # Create accounting transaction
        from accounting.models import Transaction
        Transaction.objects.create(
            user=request.user, type='subscription',
            amount=amount, status='completed',
            payment_method=request.POST.get('payment_method', 'momo'),
            reference=f"Abonnement {plan.name} ({cycle})",
        )
        messages.success(request, f'Abonnement {plan.name} activé ! Bienvenue.')
        return redirect('billing:my_subscription')
    return render(request, 'billing/subscribe.html', {'plan': plan})


@login_required
def my_subscription(request):
    sub = Subscription.objects.filter(user=request.user).first()
    payments = SubscriptionPayment.objects.filter(subscription=sub).order_by('-created_at')[:10] if sub else []
    plans = SubscriptionPlan.objects.filter(is_active=True)
    return render(request, 'billing/my_subscription.html', {
        'subscription': sub, 'payments': payments, 'plans': plans,
    })


@login_required
def payment_config(request):
    config, _ = PaymentConfig.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        config.momo_enabled = 'momo_enabled' in request.POST
        config.momo_number = request.POST.get('momo_number', '')
        config.momo_name = request.POST.get('momo_name', '')
        config.om_enabled = 'om_enabled' in request.POST
        config.om_number = request.POST.get('om_number', '')
        config.om_name = request.POST.get('om_name', '')
        config.bank_enabled = 'bank_enabled' in request.POST
        config.bank_name = request.POST.get('bank_name', '')
        config.bank_account_number = request.POST.get('bank_account_number', '')
        config.bank_account_name = request.POST.get('bank_account_name', '')
        config.cash_enabled = 'cash_enabled' in request.POST
        config.save()
        messages.success(request, 'Configuration de paiement mise à jour !')
        return redirect('billing:payment_config')
    return render(request, 'billing/payment_config.html', {'config': config})


@login_required
def boost_product(request, product_id):
    product = get_object_or_404(Product, pk=product_id, store__owner=request.user)
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
            messages.info(request, f'Votre campagne Facebook Ads sera activée sous 24h. Budget Facebook: {platform_budget:,} FCFA (Commission AfriMarket: {commission:,} FCFA).')
            boost.status = 'pending'
            boost.save()

        elif boost.platform == 'whatsapp':
            # TODO: Integrate with WhatsApp Business API
            messages.info(request, f'Votre campagne WhatsApp sera activée sous 24h. Budget WhatsApp: {platform_budget:,} FCFA (Commission AfriMarket: {commission:,} FCFA).')
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
