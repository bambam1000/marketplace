from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, F, Q, Avg
from django.db.models.functions import TruncMonth, TruncDay, TruncWeek
from django.utils import timezone
from datetime import timedelta
from .models import Commission, Payout, Expense, ProductBoost, ActivityLog
from orders.models import Order, OrderItem
from catalog.models import Product
from store.models import Store
import json


@login_required
def seller_accounting(request):
    store = getattr(request.user, 'store', None)
    if not store:
        messages.error(request, 'Vous n\'avez pas de boutique.')
        return redirect('dashboard:index')

    now = timezone.now()
    period = request.GET.get('period', '30')
    days = int(period)
    start = now - timedelta(days=days)

    items = OrderItem.objects.filter(store=store, order__created_at__gte=start, order__is_paid=True)
    total_sales = items.aggregate(total=Sum(F('price') * F('quantity')))['total'] or 0
    total_commissions = Commission.objects.filter(store=store, created_at__gte=start).aggregate(t=Sum('commission_amount'))['t'] or 0
    total_net = total_sales - total_commissions
    total_expenses = Expense.objects.filter(store=store, date__gte=start.date()).aggregate(t=Sum('amount'))['t'] or 0
    profit = total_net - total_expenses
    total_orders = items.values('order').distinct().count()
    avg_order = int(total_sales / total_orders) if total_orders else 0
    pending_payout = Commission.objects.filter(store=store, is_settled=False).aggregate(t=Sum('net_amount'))['t'] or 0

    # Monthly chart
    monthly = items.annotate(month=TruncMonth('order__created_at')).values('month').annotate(
        rev=Sum(F('price') * F('quantity'))
    ).order_by('month')
    chart_labels = [m['month'].strftime('%b %Y') for m in monthly]
    chart_data = [int(m['rev'] or 0) for m in monthly]

    # Daily chart (last 30 days)
    daily = items.annotate(day=TruncDay('order__created_at')).values('day').annotate(
        rev=Sum(F('price') * F('quantity'))
    ).order_by('day')
    daily_labels = [d['day'].strftime('%d/%m') for d in daily]
    daily_data = [int(d['rev'] or 0) for d in daily]

    # Expense by category
    expense_cats = Expense.objects.filter(store=store, date__gte=start.date()).values('category').annotate(
        total=Sum('amount')
    )
    exp_labels = [dict(Expense.CATEGORY_CHOICES).get(e['category'], e['category']) for e in expense_cats]
    exp_data = [int(e['total']) for e in expense_cats]

    # Top products by revenue
    top_products = items.values('product__name').annotate(
        rev=Sum(F('price') * F('quantity')), qty=Sum('quantity')
    ).order_by('-rev')[:5]

    # Recent transactions
    recent_orders = Order.objects.filter(
        items__store=store, is_paid=True, created_at__gte=start
    ).distinct().order_by('-created_at')[:10]

    return render(request, 'finance/seller_accounting.html', {
        'store': store, 'period': period,
        'total_sales': total_sales, 'total_commissions': total_commissions,
        'total_net': total_net, 'total_expenses': total_expenses,
        'profit': profit, 'total_orders': total_orders,
        'avg_order': avg_order, 'pending_payout': pending_payout,
        'chart_labels': json.dumps(chart_labels), 'chart_data': json.dumps(chart_data),
        'daily_labels': json.dumps(daily_labels), 'daily_data': json.dumps(daily_data),
        'exp_labels': json.dumps(exp_labels), 'exp_data': json.dumps(exp_data),
        'top_products': top_products, 'recent_orders': recent_orders,
    })


@login_required
def seller_revenue(request):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    commissions = Commission.objects.filter(store=store).order_by('-created_at')
    return render(request, 'finance/seller_revenue.html', {
        'commissions': commissions,
        'total_sales': commissions.aggregate(t=Sum('sale_amount'))['t'] or 0,
        'total_commission': commissions.aggregate(t=Sum('commission_amount'))['t'] or 0,
        'total_net': commissions.aggregate(t=Sum('net_amount'))['t'] or 0,
        'unsettled': commissions.filter(is_settled=False).aggregate(t=Sum('net_amount'))['t'] or 0,
    })


@login_required
def seller_expenses(request):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    expenses = Expense.objects.filter(store=store)
    month = request.GET.get('month')
    cat = request.GET.get('category')
    if cat: expenses = expenses.filter(category=cat)
    return render(request, 'finance/seller_expenses.html', {
        'expenses': expenses,
        'total': expenses.aggregate(t=Sum('amount'))['t'] or 0,
        'categories': Expense.CATEGORY_CHOICES,
    })


@login_required
def add_expense(request):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    if request.method == 'POST':
        Expense.objects.create(
            store=store,
            category=request.POST.get('category', 'other'),
            description=request.POST.get('description'),
            amount=request.POST.get('amount'),
            date=request.POST.get('date') or timezone.now().date(),
        )
        messages.success(request, 'Dépense enregistrée.')
        return redirect('finance:seller_expenses')
    return render(request, 'finance/add_expense.html', {'categories': Expense.CATEGORY_CHOICES})


@login_required
def seller_payouts(request):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    payouts = Payout.objects.filter(store=store)
    balance = Commission.objects.filter(store=store, is_settled=False).aggregate(t=Sum('net_amount'))['t'] or 0
    return render(request, 'finance/seller_payouts.html', {
        'payouts': payouts, 'balance': balance,
    })


@login_required
def request_payout(request):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    balance = Commission.objects.filter(store=store, is_settled=False).aggregate(t=Sum('net_amount'))['t'] or 0
    if request.method == 'POST':
        amount = int(request.POST.get('amount', 0))
        if amount > balance:
            messages.error(request, 'Montant supérieur au solde disponible.')
        elif amount < 5000:
            messages.error(request, 'Montant minimum : 5,000 FCFA.')
        else:
            Payout.objects.create(
                store=store, amount=amount,
                method=request.POST.get('method', 'momo'),
                account_number=request.POST.get('account_number', ''),
            )
            messages.success(request, f'Demande de paiement de {amount:,} FCFA envoyée.')
            return redirect('finance:seller_payouts')
    return render(request, 'finance/request_payout.html', {
        'balance': balance, 'methods': Payout.METHOD_CHOICES,
    })


@login_required
def seller_boosts(request):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    boosts = ProductBoost.objects.filter(store=store)
    products = store.products.filter(is_active=True)
    return render(request, 'finance/seller_boosts.html', {
        'boosts': boosts, 'products': products,
        'total_spent': boosts.aggregate(t=Sum('spent'))['t'] or 0,
        'total_impressions': boosts.aggregate(t=Sum('impressions'))['t'] or 0,
        'total_clicks': boosts.aggregate(t=Sum('clicks'))['t'] or 0,
    })


@login_required
def create_boost(request, product_id):
    store = getattr(request.user, 'store', None)
    if not store: return redirect('dashboard:index')
    product = get_object_or_404(Product, pk=product_id, store=store)
    if request.method == 'POST':
        boost = ProductBoost.objects.create(
            product=product, store=store,
            platform=request.POST.get('platform', 'internal'),
            budget=request.POST.get('budget', 5000),
            start_date=request.POST.get('start_date', timezone.now().date()),
            end_date=request.POST.get('end_date', (timezone.now() + timedelta(days=7)).date()),
            target_audience=request.POST.get('target_audience', ''),
        )
        messages.success(request, f'Boost créé pour "{product.name}" !')
        return redirect('finance:seller_boosts')
    return render(request, 'finance/create_boost.html', {
        'product': product, 'platforms': ProductBoost.PLATFORM_CHOICES,
    })


# ======== ADMIN FINANCE ========
@login_required
def admin_accounting(request):
    if not request.user.is_superuser and request.user.role != 'admin':
        return redirect('dashboard:index')
    now = timezone.now()
    period = request.GET.get('period', '30')
    start = now - timedelta(days=int(period))

    total_gmv = Order.objects.filter(is_paid=True, created_at__gte=start).aggregate(t=Sum('total_amount'))['t'] or 0
    total_commissions = Commission.objects.filter(created_at__gte=start).aggregate(t=Sum('commission_amount'))['t'] or 0
    total_payouts = Payout.objects.filter(status='completed', created_at__gte=start).aggregate(t=Sum('amount'))['t'] or 0
    pending_payouts = Payout.objects.filter(status='pending').aggregate(t=Sum('amount'))['t'] or 0
    total_orders = Order.objects.filter(created_at__gte=start).count()
    total_boosts = ProductBoost.objects.filter(created_at__gte=start).aggregate(t=Sum('budget'))['t'] or 0

    # Platform P&L
    platform_revenue = total_commissions + total_boosts
    platform_costs = total_payouts
    platform_profit = platform_revenue - platform_costs

    # Monthly GMV
    monthly = Order.objects.filter(is_paid=True, created_at__gte=now - timedelta(days=365)).annotate(
        month=TruncMonth('created_at')
    ).values('month').annotate(gmv=Sum('total_amount'), cnt=Count('id')).order_by('month')
    gmv_labels = [m['month'].strftime('%b %Y') for m in monthly]
    gmv_data = [int(m['gmv'] or 0) for m in monthly]
    orders_data = [m['cnt'] for m in monthly]

    # Top stores
    top_stores = Store.objects.filter(is_active=True).annotate(
        revenue=Sum(F('products__orderitem__price') * F('products__orderitem__quantity'),
                     filter=Q(products__orderitem__order__is_paid=True))
    ).order_by('-revenue')[:10]

    # Commission by store
    store_commissions = Commission.objects.filter(created_at__gte=start).values(
        'store__name'
    ).annotate(total=Sum('commission_amount')).order_by('-total')[:8]
    sc_labels = [s['store__name'] for s in store_commissions]
    sc_data = [int(s['total']) for s in store_commissions]

    return render(request, 'finance/admin_accounting.html', {
        'period': period,
        'total_gmv': total_gmv, 'total_commissions': total_commissions,
        'total_payouts': total_payouts, 'pending_payouts': pending_payouts,
        'total_orders': total_orders, 'total_boosts': total_boosts,
        'platform_revenue': platform_revenue, 'platform_profit': platform_profit,
        'gmv_labels': json.dumps(gmv_labels), 'gmv_data': json.dumps(gmv_data),
        'orders_data': json.dumps(orders_data),
        'sc_labels': json.dumps(sc_labels), 'sc_data': json.dumps(sc_data),
        'top_stores': top_stores,
    })


@login_required
def admin_commissions(request):
    if not request.user.is_superuser: return redirect('dashboard:index')
    commissions = Commission.objects.select_related('store', 'order_item__order').all()
    return render(request, 'finance/admin_commissions.html', {'commissions': commissions})


@login_required
def admin_payouts(request):
    if not request.user.is_superuser: return redirect('dashboard:index')
    payouts = Payout.objects.select_related('store').all()
    return render(request, 'finance/admin_payouts.html', {'payouts': payouts})


@login_required
def process_payout(request, pk):
    if not request.user.is_superuser: return redirect('dashboard:index')
    payout = get_object_or_404(Payout, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            payout.status = 'completed'
            payout.completed_at = timezone.now()
            # Mark commissions as settled
            unsettled = Commission.objects.filter(store=payout.store, is_settled=False)
            unsettled.update(is_settled=True)
            messages.success(request, f'Paiement {payout.reference} approuvé.')
        elif action == 'reject':
            payout.status = 'failed'
            messages.warning(request, f'Paiement {payout.reference} rejeté.')
        payout.save()
    return redirect('finance:admin_payouts')
