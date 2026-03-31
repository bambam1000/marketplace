from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth, TruncDay
from django.utils import timezone
from datetime import timedelta
from .models import Transaction, SellerWallet, PayoutRequest, Expense, PlatformStats
from orders.models import Order, OrderItem
import json


@login_required
def accounting_dashboard(request):
    user = request.user
    is_admin = user.is_superuser or user.role == 'admin'

    if is_admin:
        # Admin: platform-wide accounting
        total_revenue = Transaction.objects.filter(type='sale', status='completed').aggregate(t=Sum('amount'))['t'] or 0
        total_commissions = Transaction.objects.filter(type='commission', status='completed').aggregate(t=Sum('amount'))['t'] or 0
        total_subscriptions = Transaction.objects.filter(type='subscription', status='completed').aggregate(t=Sum('amount'))['t'] or 0
        total_boosts = Transaction.objects.filter(type='boost', status='completed').aggregate(t=Sum('amount'))['t'] or 0
        total_payouts = Transaction.objects.filter(type='payout', status='completed').aggregate(t=Sum('amount'))['t'] or 0
        total_expenses = Expense.objects.aggregate(t=Sum('amount'))['t'] or 0
        platform_income = total_commissions + total_subscriptions + total_boosts
        net_profit = platform_income - total_payouts - total_expenses
        pending_payouts = PayoutRequest.objects.filter(status='pending').count()

        # Monthly data
        monthly = Transaction.objects.filter(
            status='completed',
            created_at__gte=timezone.now() - timedelta(days=180)
        ).annotate(month=TruncMonth('created_at')).values('month').annotate(
            total=Sum('amount')
        ).order_by('month')

        recent_txn = Transaction.objects.all()[:15]
    else:
        # Seller: own accounting
        wallet, _ = SellerWallet.objects.get_or_create(user=user)
        total_revenue = wallet.total_earned
        total_commissions = wallet.total_commission_paid
        total_payouts = wallet.total_withdrawn
        total_subscriptions = 0
        total_boosts = Transaction.objects.filter(user=user, type='boost', status='completed').aggregate(t=Sum('amount'))['t'] or 0
        total_expenses = total_commissions + total_boosts
        platform_income = total_revenue - total_expenses
        net_profit = wallet.balance
        pending_payouts = PayoutRequest.objects.filter(user=user, status='pending').count()

        monthly = Transaction.objects.filter(
            user=user, status='completed',
            created_at__gte=timezone.now() - timedelta(days=180)
        ).annotate(month=TruncMonth('created_at')).values('month').annotate(
            total=Sum('amount')
        ).order_by('month')

        recent_txn = Transaction.objects.filter(user=user)[:15]

    months = [m['month'].strftime('%b %Y') for m in monthly]
    amounts = [int(m['total'] or 0) for m in monthly]

    return render(request, 'accounting/dashboard.html', {
        'is_admin': is_admin,
        'total_revenue': total_revenue,
        'total_commissions': total_commissions,
        'total_subscriptions': total_subscriptions,
        'total_boosts': total_boosts,
        'total_payouts': total_payouts,
        'total_expenses': total_expenses if is_admin else total_commissions + total_boosts,
        'platform_income': platform_income,
        'net_profit': net_profit,
        'pending_payouts': pending_payouts,
        'recent_transactions': recent_txn,
        'months_json': json.dumps(months),
        'amounts_json': json.dumps(amounts),
    })


@login_required
def transactions_list(request):
    user = request.user
    is_admin = user.is_superuser or user.role == 'admin'
    if is_admin:
        txns = Transaction.objects.all()
    else:
        txns = Transaction.objects.filter(user=user)
    type_filter = request.GET.get('type')
    if type_filter:
        txns = txns.filter(type=type_filter)
    return render(request, 'accounting/transactions.html', {
        'transactions': txns[:100],
        'type_choices': Transaction.TYPE_CHOICES,
        'is_admin': is_admin,
    })


@login_required
def wallet_view(request):
    wallet, _ = SellerWallet.objects.get_or_create(user=request.user)
    recent_txn = Transaction.objects.filter(user=request.user)[:10]
    payouts = PayoutRequest.objects.filter(user=request.user)[:10]
    return render(request, 'accounting/wallet.html', {
        'wallet': wallet,
        'transactions': recent_txn,
        'payouts': payouts,
    })


@login_required
def payout_request(request):
    wallet, _ = SellerWallet.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        amount = int(request.POST.get('amount', 0))
        if amount <= 0:
            messages.error(request, 'Montant invalide.')
        elif amount > wallet.balance:
            messages.error(request, f'Solde insuffisant. Disponible: {wallet.balance:,.0f} F')
        elif amount < 5000:
            messages.error(request, 'Retrait minimum: 5,000 F')
        else:
            PayoutRequest.objects.create(
                user=request.user,
                amount=amount,
                payment_method=request.POST.get('payment_method', 'momo'),
                account_details=request.POST.get('account_details', ''),
            )
            wallet.balance -= amount
            wallet.pending_balance += amount
            wallet.save()
            messages.success(request, f'Demande de retrait de {amount:,.0f} F envoyée !')
            return redirect('accounting:wallet')
    return render(request, 'accounting/payout_form.html', {'wallet': wallet})


@login_required
def expenses_view(request):
    if not (request.user.is_superuser or request.user.role == 'admin'):
        messages.error(request, 'Accès réservé aux administrateurs.')
        return redirect('accounting:dashboard')
    expenses = Expense.objects.all()
    if request.method == 'POST':
        Expense.objects.create(
            category=request.POST.get('category'),
            description=request.POST.get('description'),
            amount=int(request.POST.get('amount', 0)),
            date=request.POST.get('date', timezone.now().date()),
            created_by=request.user,
        )
        messages.success(request, 'Dépense enregistrée !')
        return redirect('accounting:expenses')
    cat_totals = expenses.values('category').annotate(total=Sum('amount'))
    return render(request, 'accounting/expenses.html', {
        'expenses': expenses[:50],
        'cat_totals': cat_totals,
        'categories': Expense.CATEGORY_CHOICES,
    })


@login_required
def financial_report(request):
    is_admin = request.user.is_superuser or request.user.role == 'admin'
    period = request.GET.get('period', '30')
    days = int(period)
    start_date = timezone.now() - timedelta(days=days)

    if is_admin:
        txns = Transaction.objects.filter(created_at__gte=start_date, status='completed')
    else:
        txns = Transaction.objects.filter(user=request.user, created_at__gte=start_date, status='completed')

    by_type = txns.values('type').annotate(total=Sum('amount'), count=Count('id'))
    daily = txns.annotate(day=TruncDay('created_at')).values('day').annotate(total=Sum('amount')).order_by('day')

    days_labels = [d['day'].strftime('%d/%m') for d in daily]
    days_values = [int(d['total'] or 0) for d in daily]

    return render(request, 'accounting/report.html', {
        'is_admin': is_admin,
        'period': period,
        'by_type': by_type,
        'total_period': txns.aggregate(t=Sum('amount'))['t'] or 0,
        'txn_count': txns.count(),
        'days_json': json.dumps(days_labels),
        'values_json': json.dumps(days_values),
    })
