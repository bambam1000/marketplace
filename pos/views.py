from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, F, Count
from django.utils import timezone
from decimal import Decimal
import json

from .models import POSSession, POSSale, POSSaleItem, CashMovement, POSProduct
from catalog.models import Product
from store.models import Store


@login_required
def pos_dashboard(request):
    """Dashboard principal du POS"""
    store = get_object_or_404(Store, owner=request.user)

    # Session active
    active_session = POSSession.objects.filter(
        store=store,
        status='open'
    ).first()

    # Statistiques du jour
    today = timezone.now().date()
    today_sales = POSSale.objects.filter(
        store=store,
        created_at__date=today,
        status='completed'
    )

    today_stats = {
        'sales_count': today_sales.count(),
        'total_revenue': today_sales.aggregate(total=Sum('total_amount'))['total'] or 0,
        'average_sale': today_sales.aggregate(avg=Sum('total_amount'))['avg'] or 0,
    }

    # Sessions récentes
    recent_sessions = POSSession.objects.filter(store=store)[:10]

    context = {
        'active_session': active_session,
        'today_stats': today_stats,
        'recent_sessions': recent_sessions,
    }

    return render(request, 'pos/dashboard.html', context)


@login_required
def open_session(request):
    """Ouvrir une session de caisse"""
    if request.method == 'POST':
        store = get_object_or_404(Store, owner=request.user)

        # Vérifier qu'il n'y a pas déjà une session ouverte
        existing = POSSession.objects.filter(store=store, status='open').first()
        if existing:
            return redirect('pos:interface')

        opening_cash = request.POST.get('opening_cash', 0)

        session = POSSession.objects.create(
            store=store,
            cashier=request.user,
            opening_cash=Decimal(str(opening_cash))
        )

        # Rediriger vers l'interface de caisse
        return redirect('pos:interface')

    return render(request, 'pos/open_session.html')


@login_required
def close_session(request, session_id):
    """Fermer une session de caisse"""
    session = get_object_or_404(POSSession, id=session_id, cashier=request.user)

    if request.method == 'POST':
        closing_cash = request.POST.get('closing_cash', 0)
        notes = request.POST.get('notes', '')

        session.notes = notes
        session.close_session(closing_cash)

        return redirect('pos:dashboard')

    # Calculer les totaux
    session.calculate_totals()

    context = {
        'session': session,
    }

    return render(request, 'pos/close_session.html', context)


@login_required
def pos_interface(request):
    """Interface de caisse (POS terminal)"""
    store = get_object_or_404(Store, owner=request.user)

    # Session active requise
    session = POSSession.objects.filter(store=store, status='open').first()
    if not session:
        return redirect('pos:open_session')

    # Vente en cours (panier)
    current_sale = POSSale.objects.filter(
        session=session,
        status='pending'
    ).first()

    if not current_sale:
        current_sale = POSSale.objects.create(
            session=session,
            store=store,
            cashier=request.user
        )

    # Produits pour boutons rapides
    quick_products = POSProduct.objects.filter(
        product__store=store,
        quick_button=True,
        product__is_active=True
    ).select_related('product')[:20]

    # Catégories
    categories = store.products.values('category__id', 'category__name', 'category__icon').distinct()

    context = {
        'session': session,
        'current_sale': current_sale,
        'quick_products': quick_products,
        'categories': categories,
    }

    return render(request, 'pos/interface.html', context)


@login_required
@require_http_methods(["POST"])
def add_item(request):
    """Ajouter un article à la vente"""
    data = json.loads(request.body)
    sale_id = data.get('sale_id')
    product_id = data.get('product_id')
    quantity = Decimal(str(data.get('quantity', 1)))
    barcode = data.get('barcode')

    sale = get_object_or_404(POSSale, id=sale_id, status='pending')

    # Rechercher le produit
    product = None
    if barcode:
        pos_product = POSProduct.objects.filter(barcode=barcode).first()
        if pos_product:
            product = pos_product.product
    elif product_id:
        product = get_object_or_404(Product, id=product_id)

    if not product:
        return JsonResponse({'error': 'Produit non trouvé'}, status=404)

    # Vérifier le stock
    if product.stock < quantity:
        return JsonResponse({'error': 'Stock insuffisant'}, status=400)

    # Vérifier si l'article existe déjà
    existing_item = sale.items.filter(product=product).first()
    if existing_item:
        existing_item.quantity += quantity
        existing_item.save()
        item = existing_item
    else:
        # Créer nouvel article
        item = POSSaleItem.objects.create(
            sale=sale,
            product=product,
            product_name=product.name,
            product_sku=product.sku,
            quantity=quantity,
            unit_price=product.price
        )

    return JsonResponse({
        'success': True,
        'item': {
            'id': item.id,
            'name': item.product_name,
            'quantity': float(item.quantity),
            'unit_price': float(item.unit_price),
            'total': float(item.total)
        },
        'sale_total': float(sale.total_amount)
    })


@login_required
@require_http_methods(["POST"])
def update_item(request, item_id):
    """Modifier la quantité d'un article"""
    data = json.loads(request.body)
    quantity = Decimal(str(data.get('quantity', 1)))

    item = get_object_or_404(POSSaleItem, id=item_id, sale__status='pending')

    if quantity <= 0:
        item.delete()
        return JsonResponse({'success': True, 'deleted': True})

    # Vérifier le stock
    if item.product and item.product.stock < quantity:
        return JsonResponse({'error': 'Stock insuffisant'}, status=400)

    item.quantity = quantity
    item.save()

    return JsonResponse({
        'success': True,
        'item': {
            'id': item.id,
            'quantity': float(item.quantity),
            'total': float(item.total)
        },
        'sale_total': float(item.sale.total_amount)
    })


@login_required
@require_http_methods(["POST"])
def remove_item(request, item_id):
    """Supprimer un article"""
    item = get_object_or_404(POSSaleItem, id=item_id, sale__status='pending')
    sale = item.sale

    item.delete()

    return JsonResponse({
        'success': True,
        'sale_total': float(sale.total_amount)
    })


@login_required
@require_http_methods(["POST"])
def apply_discount(request, sale_id):
    """Appliquer une remise"""
    data = json.loads(request.body)
    sale = get_object_or_404(POSSale, id=sale_id, status='pending')

    discount_percent = Decimal(str(data.get('discount_percent', 0)))
    discount_amount = Decimal(str(data.get('discount_amount', 0)))

    if discount_percent > 0:
        sale.discount_percent = discount_percent
        sale.calculate_totals()
    elif discount_amount > 0:
        sale.discount_amount = discount_amount
        sale.calculate_totals()

    return JsonResponse({
        'success': True,
        'discount_amount': float(sale.discount_amount),
        'total_amount': float(sale.total_amount)
    })


@login_required
@require_http_methods(["POST"])
def complete_sale(request, sale_id):
    """Finaliser la vente et encaisser"""
    data = json.loads(request.body)
    sale = get_object_or_404(POSSale, id=sale_id, status='pending')

    # Informations client
    sale.customer_name = data.get('customer_name', '')
    sale.customer_phone = data.get('customer_phone', '')

    # Paiements
    sale.cash_amount = Decimal(str(data.get('cash_amount', 0)))
    sale.card_amount = Decimal(str(data.get('card_amount', 0)))
    sale.mobile_money_amount = Decimal(str(data.get('mobile_money_amount', 0)))
    sale.amount_tendered = Decimal(str(data.get('amount_tendered', 0)))

    # Vérifier que le montant payé est suffisant
    total_paid = sale.cash_amount + sale.card_amount + sale.mobile_money_amount
    if total_paid < sale.total_amount:
        return JsonResponse({'error': 'Montant insuffisant'}, status=400)

    # Compléter la vente
    sale.complete_sale()

    # Enregistrer mouvements de caisse
    if sale.cash_amount > 0:
        CashMovement.objects.create(
            session=sale.session,
            type='in',
            category='sale',
            amount=sale.cash_amount,
            description=f"Vente {sale.sale_number}",
            reference=sale.sale_number,
            created_by=request.user
        )

    return JsonResponse({
        'success': True,
        'sale_number': sale.sale_number,
        'change_amount': float(sale.change_amount),
        'receipt_url': f'/pos/receipt/{sale.id}/'
    })


@login_required
def print_receipt(request, sale_id):
    """Imprimer le ticket de caisse"""
    sale = get_object_or_404(POSSale, id=sale_id)
    sale.receipt_printed = True
    sale.save()

    context = {
        'sale': sale,
        'store': sale.store,
    }

    return render(request, 'pos/receipt.html', context)


@login_required
def search_products(request):
    """Rechercher des produits (API)"""
    query = request.GET.get('q', '')
    store = get_object_or_404(Store, owner=request.user)

    products = Product.objects.filter(
        Q(name__icontains=query) | Q(sku__icontains=query),
        store=store,
        is_active=True
    )[:20]

    # Chercher aussi par code-barres
    pos_product = POSProduct.objects.filter(barcode=query).first()
    if pos_product:
        products = [pos_product.product]

    results = [{
        'id': p.id,
        'name': p.name,
        'sku': p.sku,
        'price': float(p.price),
        'stock': p.stock,
        'image': p.image.url if p.image else None
    } for p in products]

    return JsonResponse({'products': results})


@login_required
def sales_report(request):
    """Rapport des ventes"""
    store = get_object_or_404(Store, owner=request.user)

    # Filtres
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    sales = POSSale.objects.filter(store=store, status='completed')

    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)

    # Statistiques
    stats = sales.aggregate(
        total_sales=Count('id'),
        total_revenue=Sum('total_amount'),
        total_cash=Sum('cash_amount'),
        total_card=Sum('card_amount'),
        total_mobile_money=Sum('mobile_money_amount'),
    )

    # Top produits
    top_products = POSSaleItem.objects.filter(
        sale__in=sales
    ).values(
        'product__name'
    ).annotate(
        qty=Sum('quantity'),
        revenue=Sum('total')
    ).order_by('-revenue')[:10]

    context = {
        'sales': sales[:100],
        'stats': stats,
        'top_products': top_products,
        'date_from': date_from,
        'date_to': date_to,
    }

    return render(request, 'pos/sales_report.html', context)


@login_required
@require_http_methods(["POST"])
def refund_sale(request, sale_id):
    """Rembourser une vente"""
    sale = get_object_or_404(POSSale, id=sale_id)

    if sale.status != 'completed':
        return JsonResponse({'error': 'Seules les ventes complétées peuvent être remboursées'}, status=400)

    # Effectuer le remboursement
    success = sale.refund()

    if success:
        # Enregistrer mouvement de caisse
        if sale.cash_amount > 0:
            CashMovement.objects.create(
                session=sale.session,
                type='out',
                category='refund',
                amount=sale.cash_amount,
                description=f"Remboursement {sale.sale_number}",
                reference=sale.sale_number,
                created_by=request.user
            )

        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Impossible de rembourser cette vente'}, status=400)
