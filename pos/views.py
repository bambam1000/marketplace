import csv
import json
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalog.models import Product
from inventory.models import Warehouse
from . import services
from .models import CFA_DENOMINATIONS, PAYMENT_METHODS, CashMovement, CashRegister, POSProduct, POSSale, POSSaleItem, POSSession
from .services import PosError


# ───────────────────────── Accès ─────────────────────────

def pos_access(manage=False, api=False):
    """Vérifie que l'utilisateur peut utiliser (ou gérer) la caisse de sa boutique ; passe `scope` à la vue."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            scope = services.scope_for(request.user)
            allowed = scope is not None and (scope.can_manage if manage else scope.can_use)
            if not allowed:
                text = "Vous n'avez pas accès à la caisse." if not manage else 'Réservé aux responsables de caisse.'
                if api:
                    return JsonResponse({'error': text}, status=403)
                messages.error(request, text)
                return redirect('pos:dashboard' if manage and scope and scope.can_use else 'dashboard:index')
            return view(request, scope, *args, **kwargs)
        return wrapper
    return decorator


def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def _api_error(exc, status=400):
    return JsonResponse({'error': str(exc)}, status=status)


def _visible_session(scope, user, pk):
    return get_object_or_404(services.sessions_for(scope, user), pk=pk)


def _visible_sale(scope, user, pk):
    return get_object_or_404(services.sales_for(scope, user).select_related('session', 'session__register', 'warehouse', 'cashier'), pk=pk)


# ───────────────────────── Tableau de bord ─────────────────────────

@pos_access()
def pos_dashboard(request, scope):
    """Caisses de chaque entrepôt, sessions en cours, chiffres du jour."""
    store = scope.store
    if scope.can_manage:
        services.ensure_store_warehouse(store)
    warehouses = store.warehouses.filter(is_active=True)
    if scope.limit:
        warehouses = warehouses.filter(pk=scope.limit)
    for wh in warehouses:
        if not wh.cash_registers.exists():
            services.ensure_default_register(wh)

    selected = None
    raw = request.GET.get('warehouse', '')
    if raw.isdigit() and not scope.limit:
        selected = warehouses.filter(pk=raw).first()
    elif scope.limit:
        selected = warehouses.first()

    today = timezone.localdate()
    sales = services.sales_for(scope, request.user)
    if selected:
        sales = sales.filter(warehouse=selected)
    today_sales = sales.filter(completed_at__date=today)
    yesterday = services.figures(sales.filter(completed_at__date=today - timedelta(days=1)))
    today_figures = services.figures(today_sales)

    registers = services.registers_for(scope, include_inactive=scope.can_manage)
    if selected:
        registers = registers.filter(warehouse=selected)
    open_sessions = {s.register_id: s for s in POSSession.objects.filter(register__in=registers, status='open').select_related('cashier')}
    per_register = dict(today_sales.values_list('session__register').annotate(t=Sum('total_amount')))
    last_closed = {}
    for s in POSSession.objects.filter(register__in=registers, status='closed').order_by('register_id', '-closed_at'):
        last_closed.setdefault(s.register_id, s)

    groups = {}
    for reg in registers:
        reg.current = open_sessions.get(reg.pk)
        reg.today_total = per_register.get(reg.pk) or 0
        reg.last = last_closed.get(reg.pk)
        groups.setdefault(reg.warehouse, []).append(reg)

    sessions = services.sessions_for(scope, request.user)
    if selected:
        sessions = sessions.filter(warehouse=selected)
    recent = list(sessions.filter(status='closed')[:8])
    gaps = sessions.filter(status='closed', closed_at__date__gte=today - timedelta(days=30)).exclude(cash_difference=0)

    return render(request, 'pos/dashboard.html', {
        'scope': scope, 'groups': groups, 'warehouses': warehouses, 'selected': selected,
        'my_session': services.current_session(request.user, store),
        'today': today_figures, 'yesterday': yesterday,
        'open_count': len(open_sessions), 'register_count': registers.filter(is_active=True).count(),
        'recent_sessions': recent,
        'gap_total': gaps.aggregate(t=Sum('cash_difference'))['t'] or 0, 'gap_count': gaps.count(),
        'held_count': POSSale.objects.filter(session__in=sessions.filter(status='open'), status='held').count(),
    })


# ───────────────────────── Caisses (gestion) ─────────────────────────

@pos_access(manage=True)
def registers(request, scope):
    store = scope.store
    warehouses = store.warehouses.filter(is_active=True)
    if scope.limit:
        warehouses = warehouses.filter(pk=scope.limit)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:60]
        warehouse = warehouses.filter(pk=request.POST.get('warehouse') or 0).first()
        if not name or warehouse is None:
            messages.error(request, "Indiquez un nom et l'entrepôt de la caisse.")
        elif CashRegister.objects.filter(warehouse=warehouse, name__iexact=name).exists():
            messages.error(request, f'{warehouse.name} a déjà une caisse « {name} ».')
        else:
            try:
                reg = CashRegister.objects.create(
                    store=store, warehouse=warehouse, name=name,
                    default_opening_cash=services.to_decimal(request.POST.get('default_opening_cash')),
                    max_discount_percent=min(Decimal('100'), services.to_decimal(request.POST.get('max_discount_percent'), Decimal('10'))))
                messages.success(request, f'Caisse « {reg.name} » créée dans {warehouse.name}.')
            except PosError as exc:
                messages.error(request, str(exc))
        return redirect('pos:registers')

    regs = services.registers_for(scope, include_inactive=True).annotate(
        n_sessions=Count('sessions'), open_n=Count('sessions', filter=Q(sessions__status='open')))
    groups = {}
    for reg in regs:
        groups.setdefault(reg.warehouse, []).append(reg)
    for wh in warehouses:
        groups.setdefault(wh, [])
    return render(request, 'pos/registers.html', {'scope': scope, 'groups': groups, 'warehouses': warehouses})


@pos_access(manage=True)
@require_POST
def register_edit(request, scope, pk):
    reg = get_object_or_404(services.registers_for(scope, include_inactive=True), pk=pk)
    action = request.POST.get('action', 'save')
    if action == 'toggle':
        if reg.is_active and reg.open_session:
            messages.error(request, f'Fermez la session en cours de « {reg.name} » avant de la désactiver.')
        else:
            reg.is_active = not reg.is_active
            reg.save(update_fields=['is_active'])
            messages.success(request, f'Caisse « {reg.name} » {"activée" if reg.is_active else "désactivée"}.')
        return redirect('pos:registers')
    name = request.POST.get('name', '').strip()[:60]
    if not name:
        messages.error(request, 'Le nom est obligatoire.')
        return redirect('pos:registers')
    if CashRegister.objects.filter(warehouse=reg.warehouse, name__iexact=name).exclude(pk=reg.pk).exists():
        messages.error(request, f'{reg.warehouse.name} a déjà une caisse « {name} ».')
        return redirect('pos:registers')
    try:
        reg.name = name
        reg.code = request.POST.get('code', '').strip()[:20] or reg.code
        reg.default_opening_cash = max(Decimal('0'), services.to_decimal(request.POST.get('default_opening_cash')))
        reg.max_discount_percent = min(Decimal('100'), max(Decimal('0'), services.to_decimal(request.POST.get('max_discount_percent'), Decimal('10'))))
    except PosError as exc:
        messages.error(request, str(exc))
        return redirect('pos:registers')
    reg.save()
    messages.success(request, f'Caisse « {reg.name} » enregistrée.')
    return redirect('pos:registers')


# ───────────────────────── Sessions ─────────────────────────

@pos_access()
def open_session(request, scope):
    """Ouvrir une caisse. POST : register (ou warehouse → sa première caisse), opening_cash."""
    store = scope.store
    mine = services.current_session(request.user, store)
    if mine:
        return redirect('pos:interface')
    available = services.registers_for(scope)
    register = None
    raw = request.POST.get('register') or request.GET.get('register')
    if raw:
        register = available.filter(pk=raw if str(raw).isdigit() else 0).first()
        if register is None:
            messages.error(request, 'Caisse introuvable.')
            return redirect('pos:dashboard')
    elif request.method == 'POST':
        if scope.can_manage:
            services.ensure_store_warehouse(store)
        wh_raw = request.POST.get('warehouse')
        warehouses = store.warehouses.filter(is_active=True)
        if scope.limit:
            warehouses = warehouses.filter(pk=scope.limit)
        warehouse = warehouses.filter(pk=wh_raw).first() if wh_raw and wh_raw.isdigit() else warehouses.filter(is_default=True).first() or warehouses.first()
        if warehouse is None:
            messages.error(request, 'Aucun entrepôt actif : créez un entrepôt pour ouvrir une caisse.')
            return redirect('pos:dashboard')
        free = available.filter(warehouse=warehouse).exclude(sessions__status='open')
        register = free.first() or services.ensure_default_register(warehouse)

    if request.method == 'POST':
        try:
            services.open_session(request.user, scope, register, request.POST.get('opening_cash'),
                                  request.POST.get('notes', ''))
        except PosError as exc:
            messages.error(request, str(exc))
            return redirect(f"{reverse('pos:open_session')}?register={register.pk}" if register else 'pos:dashboard')
        return redirect('pos:interface')

    if register is None:
        return redirect('pos:dashboard')
    last = register.last_session
    return render(request, 'pos/open_session.html', {
        'register': register, 'busy': register.open_session, 'last': last,
        'suggested': last.closing_cash if last and last.closing_cash is not None and not register.default_opening_cash else register.default_opening_cash,
    })


@pos_access()
def close_session(request, scope, session_id):
    session = _visible_session(scope, request.user, session_id)
    if session.status != 'open':
        return redirect('pos:session_detail', session.pk)
    if session.cashier_id != request.user.pk and not scope.can_manage:
        messages.error(request, 'Seul le caissier ou un responsable peut fermer cette caisse.')
        return redirect('pos:dashboard')
    session.calculate_totals()
    if request.method == 'POST':
        count = {}
        total = Decimal('0')
        for d in CFA_DENOMINATIONS:
            n = request.POST.get(f'd{d}', '').strip()
            if n.isdigit() and int(n):
                count[str(d)] = int(n)
                total += d * int(n)
        counted = request.POST.get('closing_cash') or (total if count else None)
        try:
            services.close_session(session, request.user, scope, counted, count, request.POST.get('notes', ''))
        except PosError as exc:
            messages.error(request, str(exc))
            return redirect('pos:close_session', session.pk)
        session.refresh_from_db()
        if session.cash_difference:
            messages.warning(request, f'Caisse fermée avec un écart de {int(session.cash_difference):+} F.')
        else:
            messages.success(request, 'Caisse fermée : le tiroir est juste.')
        return redirect('pos:session_detail', session.pk)
    return render(request, 'pos/close_session.html', {
        'session': session, 'expected': session.cash_balance(), 'denominations': CFA_DENOMINATIONS,
        'breakdown': session.payment_breakdown(),
        'pending_carts': session.sales.filter(status__in=['pending', 'held']).annotate(n=Count('items')).filter(n__gt=0).count(),
    })


@pos_access()
def session_detail(request, scope, session_id):
    session = _visible_session(scope, request.user, session_id)
    if session.status == 'open':
        session.calculate_totals()
    sales = session.sales.filter(status__in=['completed', 'refunded']).select_related('cashier')
    return render(request, 'pos/session_detail.html', {
        'scope': scope, 'session': session, 'sales': sales,
        'movements': session.cash_movements.select_related('created_by').order_by('created_at'),
        'refunds': session.refunds.select_related('sale', 'created_by'),
        'breakdown': session.payment_breakdown(),
        'balance': session.cash_balance(),
        'top': (POSSaleItem.objects.filter(sale__in=sales).values('product_name')
                .annotate(qty=Sum('quantity'), revenue=Sum('total')).order_by('-revenue')[:5]),
        'can_close': session.status == 'open' and (session.cashier_id == request.user.pk or scope.can_manage),
        'categories_in': [c for c in CashMovement.CATEGORY_CHOICES if c[0] in CashMovement.MANUAL_IN],
        'categories_out': [c for c in CashMovement.CATEGORY_CHOICES if c[0] in CashMovement.MANUAL_OUT],
    })


@pos_access()
def session_report(request, scope, session_id):
    """Ticket X (caisse ouverte : lecture intermédiaire) ou Z (clôture)."""
    session = _visible_session(scope, request.user, session_id)
    if session.status == 'open':
        session.calculate_totals()
    sales = session.sales.filter(status__in=['completed', 'refunded'])
    return render(request, 'pos/session_report.html', {
        'session': session, 'store': session.store, 'breakdown': session.payment_breakdown(),
        'balance': session.cash_balance(), 'now': timezone.localtime(),
        'kind': 'X' if session.status == 'open' else 'Z',
        'movements': session.cash_movements.exclude(category__in=['opening', 'sale']).order_by('created_at'),
        'top': (POSSaleItem.objects.filter(sale__in=sales).values('product_name')
                .annotate(qty=Sum('quantity'), revenue=Sum('total')).order_by('-revenue')[:10]),
        'count_rows': [(int(d), n, int(d) * n) for d, n in sorted(session.cash_count.items(), key=lambda x: -int(x[0]))],
    })


@pos_access()
@require_POST
def cash_movement(request, scope, session_id):
    session = _visible_session(scope, request.user, session_id)
    if session.cashier_id != request.user.pk and not scope.can_manage:
        messages.error(request, 'Seul le caissier ou un responsable peut saisir un mouvement.')
        return redirect('pos:session_detail', session.pk)
    try:
        move = services.add_cash_movement(session, request.user, request.POST.get('type'), request.POST.get('category'),
                                          request.POST.get('amount'), request.POST.get('description', ''))
        messages.success(request, f'{move.get_type_display()} de {int(move.amount)} F enregistrée.')
    except PosError as exc:
        messages.error(request, str(exc))
    if request.POST.get('next') == 'interface':
        return redirect('pos:interface')
    return redirect('pos:session_detail', session.pk)


# ───────────────────────── Interface de caisse ─────────────────────────

def _catalogue(store, warehouse):
    from inventory.models import ProductStock
    products = store.products.filter(is_active=True).select_related('category').order_by('name')[:1500]
    here = dict(ProductStock.objects.filter(warehouse=warehouse, product__store=store).values_list('product_id', 'quantity')) if warehouse else {}
    barcodes = dict(POSProduct.objects.filter(product__store=store).exclude(barcode__isnull=True).values_list('product_id', 'barcode'))
    rows = []
    for p in products:
        stock = min(here.get(p.pk, 0), p.stock) if warehouse else p.stock
        rows.append({'id': p.pk, 'name': p.name, 'sku': p.sku or '', 'barcode': barcodes.get(p.pk, ''),
                     'price': int(p.price), 'stock': stock, 'cat': p.category.name if p.category_id else 'Autres',
                     'img': p.image.url if p.image else ''})
    return rows


def cart_payload(sale):
    sale.refresh_from_db()
    items = [{'id': i.pk, 'product_id': i.product_id, 'name': i.product_name, 'sku': i.product_sku,
              'quantity': int(i.quantity), 'unit_price': int(i.unit_price), 'total': int(i.total)}
             for i in sale.items.all()]
    held = [{'id': s.pk, 'label': s.held_label, 'total': int(s.total_amount), 'n': s.n}
            for s in sale.session.sales.filter(status='held').annotate(n=Count('items')).order_by('created_at')]
    return {'sale_id': sale.pk, 'items': items, 'subtotal': int(sale.subtotal), 'discount_amount': int(sale.discount_amount),
            'discount_percent': float(sale.discount_percent), 'tax': int(sale.tax_amount), 'total': int(sale.total_amount),
            'sale_total': float(sale.total_amount), 'held': held}


@pos_access()
def pos_interface(request, scope):
    session = services.current_session(request.user, scope.store)
    if session is None:
        messages.info(request, 'Ouvrez une caisse pour commencer à encaisser.')
        return redirect('pos:dashboard')
    sale = services.open_sale(session, request.user)
    session.calculate_totals()
    catalogue = _catalogue(scope.store, session.warehouse)
    return render(request, 'pos/interface.html', {
        'session': session, 'current_sale': sale, 'cart': cart_payload(sale), 'catalogue': catalogue,
        'categories': sorted({p['cat'] for p in catalogue}), 'scope': scope,
        'max_discount': session.register.max_discount_percent if session.register else 100,
        'balance': session.cash_balance(),
        'categories_in': [c for c in CashMovement.CATEGORY_CHOICES if c[0] in CashMovement.MANUAL_IN],
        'categories_out': [c for c in CashMovement.CATEGORY_CHOICES if c[0] in CashMovement.MANUAL_OUT],
    })


@pos_access(api=True)
@require_POST
def add_item(request, scope):
    data = _json(request)
    try:
        sale = services.editable_sale(request.user, data.get('sale_id'))
        product = None
        code = str(data.get('barcode') or '').strip()
        if code:
            config = POSProduct.objects.filter(barcode=code, product__store=scope.store).select_related('product').first()
            product = config.product if config else scope.store.products.filter(sku__iexact=code, is_active=True).first()
        elif data.get('product_id'):
            product = scope.store.products.filter(pk=data.get('product_id')).first()
        if product is None:
            return JsonResponse({'error': 'Produit non trouvé'}, status=404)
        item = services.add_item(sale, product, data.get('quantity', 1))
    except PosError as exc:
        return _api_error(exc)
    payload = cart_payload(sale)
    payload.update(success=True, item={'id': item.pk, 'name': item.product_name, 'quantity': float(item.quantity),
                                       'unit_price': float(item.unit_price), 'total': float(item.total)})
    return JsonResponse(payload)


def _editable_item(request, item_id):
    item = (POSSaleItem.objects.select_related('sale', 'sale__session', 'product')
            .filter(pk=item_id, sale__status__in=['pending', 'held'], sale__session__status='open',
                    sale__session__cashier=request.user).first())
    if item is None:
        raise PosError('Article introuvable.')
    return item


@pos_access(api=True)
@require_POST
def update_item(request, scope, item_id):
    try:
        item = _editable_item(request, item_id)
        sale = item.sale
        result = services.set_quantity(item, _json(request).get('quantity', 1))
    except PosError as exc:
        return _api_error(exc)
    payload = cart_payload(sale)
    payload.update(success=True, deleted=result is None)
    if result is not None:
        payload['item'] = {'id': result.pk, 'quantity': float(result.quantity), 'total': float(result.total)}
    return JsonResponse(payload)


@pos_access(api=True)
@require_POST
def remove_item(request, scope, item_id):
    try:
        item = _editable_item(request, item_id)
    except PosError as exc:
        return _api_error(exc, 404)
    sale = item.sale
    item.delete()
    sale.calculate_totals()
    payload = cart_payload(sale)
    payload['success'] = True
    return JsonResponse(payload)


@pos_access(api=True)
@require_POST
def apply_discount(request, scope, sale_id):
    data = _json(request)
    try:
        sale = services.editable_sale(request.user, sale_id)
        services.apply_discount(sale, scope, data.get('discount_percent'), data.get('discount_amount'))
    except PosError as exc:
        return _api_error(exc)
    payload = cart_payload(sale)
    payload.update(success=True, total_amount=payload['total'])
    return JsonResponse(payload)


@pos_access(api=True)
@require_POST
def clear_cart(request, scope, sale_id):
    try:
        sale = services.editable_sale(request.user, sale_id)
    except PosError as exc:
        return _api_error(exc)
    sale.items.all().delete()
    sale.discount_percent = 0
    sale.discount_amount = 0
    sale.calculate_totals()
    payload = cart_payload(sale)
    payload['success'] = True
    return JsonResponse(payload)


@pos_access(api=True)
@require_POST
def hold_sale(request, scope, sale_id):
    try:
        sale = services.editable_sale(request.user, sale_id)
        services.hold_sale(sale, _json(request).get('label', ''))
    except PosError as exc:
        return _api_error(exc)
    payload = cart_payload(services.open_sale(sale.session, request.user))
    payload['success'] = True
    return JsonResponse(payload)


@pos_access(api=True)
@require_POST
def resume_sale(request, scope, sale_id):
    session = services.current_session(request.user, scope.store)
    if session is None:
        return _api_error('La caisse est fermée.')
    try:
        sale = services.resume_sale(session, request.user, sale_id)
    except PosError as exc:
        return _api_error(exc)
    payload = cart_payload(sale)
    payload['success'] = True
    return JsonResponse(payload)


@pos_access(api=True)
@require_POST
def complete_sale(request, scope, sale_id):
    data = _json(request)
    try:
        sale = services.editable_sale(request.user, sale_id)
        services.checkout(sale, request.user, cash=data.get('cash_amount'), card=data.get('card_amount'),
                          mobile_money=data.get('mobile_money_amount'), customer_name=data.get('customer_name', ''),
                          customer_phone=data.get('customer_phone', ''), reference=data.get('payment_reference', ''))
    except PosError as exc:
        return _api_error(exc)
    next_sale = services.open_sale(sale.session, request.user)
    return JsonResponse({
        'success': True, 'sale_number': sale.sale_number, 'total': int(sale.total_amount),
        'change_amount': float(sale.change_amount),
        'receipt_url': reverse('pos:print_receipt', args=[sale.pk]),
        'cart': cart_payload(next_sale), 'balance': int(sale.session.cash_balance()),
    })


@pos_access(api=True)
def search_products(request, scope):
    query = request.GET.get('q', '').strip()
    session = services.current_session(request.user, scope.store)
    warehouse = session.warehouse if session else None
    products = scope.store.products.filter(is_active=True)
    config = POSProduct.objects.filter(barcode=query, product__store=scope.store).first() if query else None
    if config:
        products = products.filter(pk=config.product_id)
    else:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query))[:20]
    return JsonResponse({'products': [{
        'id': p.id, 'name': p.name, 'sku': p.sku, 'price': float(p.price),
        'stock': services.warehouse_stock(p, warehouse), 'image': p.image.url if p.image else None,
    } for p in products]})


# ───────────────────────── Ventes, remboursements, tickets ─────────────────────────

@pos_access()
def sale_detail(request, scope, sale_id):
    sale = _visible_sale(scope, request.user, sale_id)
    return render(request, 'pos/sale_detail.html', {
        'scope': scope, 'sale': sale, 'items': sale.items.all(),
        'refunds': sale.refunds.select_related('session', 'session__register', 'created_by').prefetch_related('items__sale_item'),
        'my_session': services.current_session(request.user, scope.store),
        'methods': PAYMENT_METHODS,
    })


@pos_access(manage=True, api=True)
@require_POST
def refund_sale(request, scope, sale_id):
    """Remboursement : JSON {lines: {ligne: qté}, method, reason} ou formulaire (qty_<ligne>)."""
    sale = _visible_sale(scope, request.user, sale_id)
    is_form = request.content_type != 'application/json'
    if is_form:
        lines = {key[4:]: val for key, val in request.POST.items() if key.startswith('qty_') and val.strip()}
        data = {'lines': lines or None, 'method': request.POST.get('method') or None,
                'reason': request.POST.get('reason', ''), 'restock': request.POST.get('restock') == 'on'}
    else:
        data = _json(request)
    try:
        refund = services.refund_sale(sale, request.user, lines=data.get('lines') or None, method=data.get('method') or None,
                                      reason=data.get('reason', ''), restock=data.get('restock', True))
    except PosError as exc:
        if is_form:
            messages.error(request, str(exc))
            return redirect('pos:sale_detail', sale.pk)
        return _api_error(exc)
    if is_form:
        messages.success(request, f'Remboursement {refund.refund_number} de {int(refund.amount)} F effectué ({refund.get_method_display()}).')
        return redirect('pos:sale_detail', sale.pk)
    return JsonResponse({'success': True, 'refund_number': refund.refund_number, 'amount': float(refund.amount)})


@pos_access()
def print_receipt(request, scope, sale_id):
    sale = _visible_sale(scope, request.user, sale_id)
    if not sale.receipt_printed:
        sale.receipt_printed = True
        sale.save(update_fields=['receipt_printed'])
    return render(request, 'pos/receipt.html', {'sale': sale, 'store': sale.store, 'items': sale.items.all(),
                                                'refunds': sale.refunds.all()})


# ───────────────────────── Rapports ─────────────────────────

def _parse_date(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


@pos_access()
def sales_report(request, scope):
    today = timezone.localdate()
    date_from = _parse_date(request.GET.get('date_from'), today.replace(day=1))
    date_to = _parse_date(request.GET.get('date_to'), today)
    sales = (services.sales_for(scope, request.user).filter(status__in=['completed', 'refunded'],
             completed_at__date__gte=date_from, completed_at__date__lte=date_to)
             .select_related('cashier', 'session__register', 'warehouse'))

    warehouses = scope.store.warehouses.all()
    if scope.limit:
        warehouses = warehouses.filter(pk=scope.limit)
    regs = services.registers_for(scope, include_inactive=True)
    f = {k: request.GET.get(k, '') for k in ('warehouse', 'register', 'cashier', 'payment', 'q')}
    if f['warehouse'].isdigit():
        sales = sales.filter(warehouse_id=f['warehouse'])
        regs = regs.filter(warehouse_id=f['warehouse'])
    if f['register'].isdigit():
        sales = sales.filter(session__register_id=f['register'])
    if f['cashier'].isdigit() and scope.can_manage:
        sales = sales.filter(cashier_id=f['cashier'])
    if f['payment'] == 'cash':
        sales = sales.filter(cash_amount__gt=0)
    elif f['payment'] == 'card':
        sales = sales.filter(card_amount__gt=0)
    elif f['payment'] == 'mobile_money':
        sales = sales.filter(mobile_money_amount__gt=0)
    elif f['payment'] == 'refunded':
        sales = sales.filter(refunded_amount__gt=0)
    if f['q']:
        sales = sales.filter(Q(sale_number__icontains=f['q']) | Q(customer_name__icontains=f['q']) | Q(customer_phone__icontains=f['q']))

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="ventes_caisse_{date_from}_{date_to}.csv"'
        response.write('﻿')
        w = csv.writer(response, delimiter=';')
        w.writerow(['N° vente', 'Date', 'Entrepôt', 'Caisse', 'Caissier', 'Client', 'Sous-total', 'Remise', 'TVA', 'Total',
                    'Espèces (net)', 'Carte', 'Mobile Money', 'Remboursé', 'Statut'])
        for s in sales.order_by('completed_at'):
            w.writerow([s.sale_number, timezone.localtime(s.completed_at).strftime('%d/%m/%Y %H:%M'),
                        s.warehouse.name if s.warehouse else '', s.session.register.name if s.session.register else '',
                        s.cashier.display_name, s.customer_name, int(s.subtotal), int(s.discount_amount), int(s.tax_amount),
                        int(s.total_amount), int(s.cash_amount - s.change_amount), int(s.card_amount),
                        int(s.mobile_money_amount), int(s.refunded_amount), s.get_status_display()])
        return response

    figures = services.figures(sales)
    span = (date_to - date_from).days
    previous = services.figures(services.sales_for(scope, request.user).filter(
        completed_at__date__gte=date_from - timedelta(days=span + 1), completed_at__date__lt=date_from))

    by_day = {d: 0 for d in (date_from + timedelta(days=i) for i in range(min(span, 92) + 1))}
    for row in sales.values('completed_at__date').annotate(t=Sum('total_amount'), r=Sum('refunded_amount')):
        if row['completed_at__date'] in by_day:
            by_day[row['completed_at__date']] = int((row['t'] or 0) - (row['r'] or 0))

    by_register = (sales.values('session__register__name', 'warehouse__name')
                   .annotate(n=Count('id'), t=Sum('total_amount'), r=Sum('refunded_amount')).order_by('-t'))
    by_cashier = (sales.values('cashier__first_name', 'cashier__last_name', 'cashier__username')
                  .annotate(n=Count('id'), t=Sum('total_amount'), r=Sum('refunded_amount'), d=Sum('discount_amount')).order_by('-t'))
    top_products = (POSSaleItem.objects.filter(sale__in=sales).values('product_name')
                    .annotate(qty=Sum('quantity'), back=Sum('refunded_quantity'), revenue=Sum('total')).order_by('-revenue')[:10])
    hours = [0] * 24
    for row in sales.values('completed_at').order_by():
        hours[timezone.localtime(row['completed_at']).hour] += 1

    from accounts.models import User
    cashiers = User.objects.filter(pk__in=services.sales_for(scope, request.user).values('cashier')).order_by('first_name')
    page = Paginator(sales.order_by('-completed_at'), 30).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'pos/sales_report.html', {
        'scope': scope, 'sales': page, 'f': f, 'stats': figures, 'previous': previous,
        'date_from': date_from, 'date_to': date_to, 'warehouses': warehouses, 'registers': regs, 'cashiers': cashiers,
        'by_register': by_register, 'by_cashier': by_cashier, 'top_products': top_products,
        'chart': {'labels': [d.strftime('%d/%m') for d in by_day], 'values': list(by_day.values()), 'hours': hours},
        'querystring': query.urlencode(),
    })


@pos_access()
def sessions_list(request, scope):
    sessions = services.sessions_for(scope, request.user)
    f = {k: request.GET.get(k, '') for k in ('warehouse', 'register', 'status', 'gap')}
    if f['warehouse'].isdigit():
        sessions = sessions.filter(warehouse_id=f['warehouse'])
    if f['register'].isdigit():
        sessions = sessions.filter(register_id=f['register'])
    if f['status'] in ('open', 'closed'):
        sessions = sessions.filter(status=f['status'])
    if f['gap'] == '1':
        sessions = sessions.filter(status='closed').exclude(cash_difference=0)
    warehouses = scope.store.warehouses.all()
    if scope.limit:
        warehouses = warehouses.filter(pk=scope.limit)
    return render(request, 'pos/sessions.html', {
        'scope': scope, 'sessions': Paginator(sessions, 25).get_page(request.GET.get('page')), 'f': f,
        'warehouses': warehouses, 'registers': services.registers_for(scope, include_inactive=True),
        'gap_sum': sessions.filter(status='closed').aggregate(t=Sum('cash_difference'))['t'] or 0,
    })
