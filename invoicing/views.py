from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, FileResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.core.files.base import ContentFile
from datetime import timedelta
from decimal import Decimal
from .models import Invoice, InvoiceItem, InvoiceSettings, DeliveryNote, PaymentReceipt
from .pdf_generator import generate_invoice_pdf, generate_delivery_note_pdf, generate_payment_receipt_pdf
from orders.models import Order
from store import access


def _invoicing_scope(request, permission):
    """(boutique, entrepôt limite) si l'utilisateur a la permission, sinon (None, None).
    Propriétaire : tout. Employé : permission de son rôle, limité à son entrepôt s'il en a un."""
    store = access.acting_store(request.user)
    if store is None or not access.has_perm(request.user, store, permission):
        return None, None
    return store, access.member_warehouse_id(request.user, store)


def _visible_invoices(store, limit):
    invoices = Invoice.objects.filter(store=store)
    return invoices.filter(warehouse_id=limit) if limit else invoices


def _deny(request):
    messages.error(request, "Vous n'avez pas accès à la facturation.")
    return redirect('dashboard:index')


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value, default='0'):
    from decimal import InvalidOperation
    try:
        d = Decimal(str(value).strip() or default)
    except (InvalidOperation, ValueError):
        d = Decimal(default)
    return d if d >= 0 else Decimal(default)


def _to_date(value, default=None):
    from datetime import datetime
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except ValueError:
        return default


def _save_items(request, invoice):
    """Lignes item_description[] / item_quantity[] / item_unit_price[] ; lignes vides ou invalides ignorées."""
    descriptions = request.POST.getlist('item_description[]')
    quantities = request.POST.getlist('item_quantity[]')
    unit_prices = request.POST.getlist('item_unit_price[]')
    for i, desc in enumerate(descriptions):
        if not desc.strip():
            continue
        qty = _to_int(quantities[i] if i < len(quantities) else 1, 1)
        InvoiceItem.objects.create(
            invoice=invoice,
            description=desc.strip()[:500],
            quantity=max(qty, 1),
            unit_price=_to_decimal(unit_prices[i] if i < len(unit_prices) else 0),
            order=i,
        )


@login_required
def invoices_dashboard(request):
    """Dashboard des factures"""
    store, limit = _invoicing_scope(request, 'invoicing.view')
    if store is None:
        return _deny(request)
    now = timezone.now()
    last_30_days = now - timedelta(days=30)

    # Créer les paramètres de facturation si inexistants
    invoice_settings, created = InvoiceSettings.objects.get_or_create(
        store=store,
        defaults={
            'company_name': store.name,
            'address': store.address,
            'city': store.city,
            'phone': store.phone,
            'email': store.email,
        }
    )

    # Statistiques
    invoices = _visible_invoices(store, limit)
    stats = {
        'total_invoices': invoices.count(),
        'draft': invoices.filter(status='draft').count(),
        'sent': invoices.filter(status='sent').count(),
        'paid': invoices.filter(status='paid').count(),
        'overdue': invoices.filter(status='sent', due_date__lt=now.date()).count(),
        'total_revenue': invoices.filter(status='paid').aggregate(t=Sum('total_amount'))['t'] or 0,
        'pending_amount': invoices.filter(status='sent').aggregate(t=Sum('total_amount'))['t'] or 0,
        'this_month': invoices.filter(created_at__gte=last_30_days).count(),
    }

    # Factures récentes
    recent_invoices = invoices.select_related('customer').order_by('-created_at')[:10]

    context = {
        'stats': stats,
        'recent_invoices': recent_invoices,
        'invoice_settings': invoice_settings,
    }
    return render(request, 'invoicing/dashboard.html', context)


@login_required
def invoices_list(request):
    """Liste des factures (filtrable par entrepôt, statut, type, recherche)"""
    from django.core.paginator import Paginator
    from django.db.models import Prefetch
    from orders.models import OrderItem
    store, limit = _invoicing_scope(request, 'invoicing.view')
    if store is None:
        return _deny(request)

    warehouse, invalid = access.resolve_warehouse(request, store, limit)
    scoped = _visible_invoices(store, limit)
    if invalid:
        scoped = scoped.none()
    elif warehouse:
        scoped = scoped.filter(warehouse=warehouse)
    today = timezone.localdate()

    invoices = scoped.select_related('customer', 'order')
    status = request.GET.get('status', '')
    if status == 'overdue':
        invoices = invoices.filter(status='sent', due_date__lt=today)
    elif status:
        invoices = invoices.filter(status=status)
    invoice_type = request.GET.get('type')
    if invoice_type:
        invoices = invoices.filter(invoice_type=invoice_type)
    search = request.GET.get('q', '').strip()
    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search) |
            Q(customer_name__icontains=search) |
            Q(customer_email__icontains=search)
        )
    page_obj = Paginator(invoices.order_by('-created_at'), 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)

    # Statistiques sur le périmètre affiché (entrepôt compris)
    stats = scoped.aggregate(
        total_count=Count('id'),
        paid_count=Count('id', filter=Q(status='paid')),
        overdue_count=Count('id', filter=Q(status='sent', due_date__lt=today)),
        pending_amount=Sum('total_amount', filter=Q(status='sent')),
        total_revenue=Sum('total_amount', filter=Q(status='paid')),
    )

    # Ventes payées sans facture, pour pré-remplir la modale : uniquement les lignes de cette boutique
    can_create = access.stock_access(request.user, store, 'invoicing.create', warehouse) if (limit or warehouse) \
        else access.has_perm(request.user, store, 'invoicing.create')
    recent_sales = []
    if can_create:
        my_items = OrderItem.objects.filter(store=store).select_related('product')
        if warehouse:
            my_items = my_items.filter(warehouse=warehouse)
        recent_sales = (Order.objects.filter(pk__in=my_items.filter(order__is_paid=True).values('order'))
                        .exclude(invoices__store=store).select_related('buyer')
                        .prefetch_related(Prefetch('items', queryset=my_items, to_attr='my_items'))
                        .order_by('-created_at')[:30])

    context = {
        'invoices': page_obj,
        'page_obj': page_obj,
        'status_filter': status,
        'type_filter': invoice_type,
        'search': search,
        'total_count': stats['total_count'],
        'paid_count': stats['paid_count'],
        'overdue_count': stats['overdue_count'],
        'pending_amount': stats['pending_amount'] or 0,
        'total_revenue': stats['total_revenue'] or 0,
        'recent_sales': recent_sales,
        'today': today.isoformat(),
        'today_date': today,
        'my_products': store.products.filter(is_active=True) if can_create else [],
        'warehouse': warehouse,
        'warehouses': access.selectable_warehouses(store, limit),
        'wh_query': f'warehouse={warehouse.pk}&' if warehouse else '',
        'base_query': params.urlencode(),
        'can_create': can_create,
        'can_manage': access.has_perm(request.user, store, 'invoicing.create'),
    }
    return render(request, 'invoicing/invoices_list.html', context)


@login_required
def invoice_create(request):
    """Créer une facture manuelle (rattachée à l'entrepôt de la page si précisé)"""
    from django.db import transaction
    from inventory.models import Warehouse
    store, limit = _invoicing_scope(request, 'invoicing.create')
    if store is None:
        return _deny(request)
    settings_obj, _ = InvoiceSettings.objects.get_or_create(store=store)

    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        if not customer_name:
            messages.error(request, 'Le nom du client est requis.')
            return redirect('invoicing:invoices')
        warehouse_id = limit or request.POST.get('warehouse') or None
        warehouse = Warehouse.objects.filter(pk=warehouse_id, store=store).first() if warehouse_id else None
        invoice_type = request.POST.get('invoice_type', 'standard')
        if invoice_type not in dict(Invoice.INVOICE_TYPE_CHOICES):
            invoice_type = 'standard'
        issue_date = _to_date(request.POST.get('issue_date'), timezone.localdate())
        with transaction.atomic():
            invoice = Invoice.objects.create(
                store=store,
                warehouse=warehouse,
                invoice_number=settings_obj.get_next_invoice_number(),
                invoice_type=invoice_type,
                customer_name=customer_name,
                customer_email=request.POST.get('customer_email', '').strip(),
                customer_phone=request.POST.get('customer_phone', '').strip(),
                customer_address=request.POST.get('customer_address', '').strip(),
                issue_date=issue_date,
                due_date=_to_date(request.POST.get('due_date')),
                notes=request.POST.get('notes', ''),
                shipping_amount=_to_decimal(request.POST.get('shipping_amount')),
                discount_amount=_to_decimal(request.POST.get('discount_amount')),
                status='draft',
            )
            _save_items(request, invoice)
            invoice.calculate_totals()
        messages.success(request, f'Facture {invoice.invoice_number} créée !')
        return redirect('invoicing:invoice_detail', pk=invoice.pk)

    return render(request, 'invoicing/invoice_form.html')


@login_required
def invoice_from_order(request, order_number):
    """Créer une facture depuis une commande (lignes de la boutique uniquement)"""
    store, limit = _invoicing_scope(request, 'invoicing.create')
    if store is None:
        return _deny(request)
    order = get_object_or_404(Order, order_number=order_number)
    my_items = order.items.filter(store=store).select_related('product', 'warehouse')
    if limit:
        my_items = my_items.filter(warehouse_id=limit)
    if not my_items.exists():
        # Sans ce contrôle, on pouvait facturer (et lire les coordonnées du client) d'une commande d'un autre vendeur
        messages.error(request, "Cette commande ne contient aucun de vos produits.")
        return redirect('invoicing:invoices')
    settings_obj, _ = InvoiceSettings.objects.get_or_create(store=store)

    # Vérifier si une facture existe déjà
    existing = Invoice.objects.filter(order=order, store=store).first()
    if existing:
        messages.info(request, 'Une facture existe déjà pour cette commande.')
        return redirect('invoicing:invoice_detail', pk=existing.pk)

    # Créer la facture (hérite l'entrepôt de la commande)
    invoice = Invoice.objects.create(
        store=store,
        invoice_number=settings_obj.get_next_invoice_number(),
        order=order,
        warehouse=my_items.first().warehouse,
        customer=order.buyer,
        customer_name=order.buyer.get_full_name() or order.buyer.username,
        customer_email=order.buyer.email,
        customer_phone=getattr(order.buyer, 'phone', ''),
        customer_address=order.shipping_address or '',
        issue_date=timezone.now().date(),
        shipping_amount=order.shipping_cost or 0,
        status='draft' if not order.is_paid else 'paid',
    )

    # Ajouter les articles de la commande liés au vendeur
    for item in my_items:
        InvoiceItem.objects.create(
            invoice=invoice,
            product=item.product,
            description=item.product.name,
            quantity=item.quantity,
            unit_price=item.price,
        )

    invoice.calculate_totals()

    if order.is_paid:
        invoice.mark_as_paid()

    messages.success(request, f'Facture {invoice.invoice_number} créée depuis la commande !')
    return redirect('invoicing:invoice_detail', pk=invoice.pk)


@login_required
def invoice_detail(request, pk):
    """Détail d'une facture"""
    store, limit = _invoicing_scope(request, 'invoicing.view')
    if store is None:
        return _deny(request)

    invoice = get_object_or_404(_visible_invoices(store, limit), pk=pk)

    context = {
        'invoice': invoice,
        'can_manage': access.has_perm(request.user, store, 'invoicing.create'),
    }
    return render(request, 'invoicing/invoice_detail.html', context)


@login_required
def invoice_edit(request, pk):
    """Modifier une facture"""
    store, limit = _invoicing_scope(request, 'invoicing.create')
    if store is None:
        return _deny(request)

    invoice = get_object_or_404(_visible_invoices(store, limit), pk=pk)

    if invoice.status == 'paid':
        messages.error(request, 'Impossible de modifier une facture payée.')
        return redirect('invoicing:invoice_detail', pk=pk)

    if request.method == 'POST':
        from django.db import transaction
        invoice.customer_name = request.POST.get('customer_name', '').strip() or invoice.customer_name
        invoice.customer_email = request.POST.get('customer_email', '').strip()
        invoice.customer_phone = request.POST.get('customer_phone', '').strip()
        invoice.customer_address = request.POST.get('customer_address', '').strip()
        invoice.issue_date = _to_date(request.POST.get('issue_date'), invoice.issue_date)
        invoice.due_date = _to_date(request.POST.get('due_date'))
        invoice.notes = request.POST.get('notes', '')
        invoice.shipping_amount = _to_decimal(request.POST.get('shipping_amount'))
        invoice.discount_amount = _to_decimal(request.POST.get('discount_amount'))
        with transaction.atomic():
            invoice.save()
            # Remplacer les articles
            invoice.items.all().delete()
            _save_items(request, invoice)
            invoice.calculate_totals()
        messages.success(request, 'Facture modifiée !')
        return redirect('invoicing:invoice_detail', pk=pk)

    context = {
        'invoice': invoice,
    }
    return render(request, 'invoicing/invoice_form.html', context)


@login_required
def invoice_generate_pdf(request, pk):
    """Générer et télécharger le PDF d'une facture"""
    store, limit = _invoicing_scope(request, 'invoicing.view')
    if store is None:
        return _deny(request)

    invoice = get_object_or_404(_visible_invoices(store, limit), pk=pk)

    # Générer le PDF
    pdf_buffer = generate_invoice_pdf(invoice)

    # Sauvegarder le PDF dans le modèle
    pdf_filename = f"facture_{invoice.invoice_number}.pdf"
    invoice.pdf_file.save(pdf_filename, ContentFile(pdf_buffer.read()), save=True)

    # Retourner le PDF pour téléchargement
    pdf_buffer.seek(0)
    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{pdf_filename}"'
    return response


@login_required
def invoice_send(request, pk):
    """Envoyer une facture par email"""
    store, limit = _invoicing_scope(request, 'invoicing.create')
    if store is None:
        return _deny(request)
    if request.method != 'POST':
        # Action qui modifie la facture : formulaire POST (protégé CSRF) obligatoire
        return redirect('invoicing:invoice_detail', pk=pk)

    invoice = get_object_or_404(_visible_invoices(store, limit), pk=pk)

    # Générer le PDF si nécessaire
    if not invoice.pdf_file:
        pdf_buffer = generate_invoice_pdf(invoice)
        pdf_filename = f"facture_{invoice.invoice_number}.pdf"
        invoice.pdf_file.save(pdf_filename, ContentFile(pdf_buffer.read()), save=True)

    # Envoyer la facture par email au client (PDF en pièce jointe)
    if invoice.customer_email:
        try:
            from django.core.mail import EmailMessage
            html = f'''<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#fff;">
    <div style="background:#ff6a00;padding:24px;text-align:center;">
        <div style="color:#fff;font-size:22px;font-weight:800;">{invoice.store.name}</div>
    </div>
    <div style="padding:32px 28px;color:#333;font-size:14px;line-height:1.7;">
        <p>Bonjour {invoice.customer_name},</p>
        <p>Veuillez trouver ci-joint votre facture <strong>{invoice.invoice_number}</strong> d'un montant de <strong>{invoice.total_amount:.0f} FCFA</strong>.</p>
        <p>Merci de votre confiance.</p>
    </div>
    <div style="background:#f9fafb;padding:18px;text-align:center;font-size:11px;color:#98a2b3;">
        {invoice.store.name} · Comptoir
    </div>
</div>
</body></html>'''
            email = EmailMessage(
                f'Facture {invoice.invoice_number} — {invoice.store.name}',
                html,
                settings.DEFAULT_FROM_EMAIL,
                [invoice.customer_email],
            )
            email.content_subtype = 'html'
            if invoice.pdf_file:
                invoice.pdf_file.open('rb')
                email.attach(f'facture_{invoice.invoice_number}.pdf', invoice.pdf_file.read(), 'application/pdf')
                invoice.pdf_file.close()
            email.send(fail_silently=True)
        except Exception:
            pass

    # Notification interne si le client a un compte
    if invoice.customer:
        from messaging.utils import notify
        notify(
            invoice.customer, 'invoice',
            f'Facture {invoice.invoice_number} reçue',
            f'{invoice.store.name} vous a envoyé une facture de {invoice.total_amount:.0f} FCFA.',
            url=f'/commandes/',
        )

    invoice.status = 'sent'
    invoice.sent_at = timezone.now()
    invoice.save()

    from whatsapp.services import send_invoice
    wa = send_invoice(invoice)
    messages.success(request, f'Facture {invoice.invoice_number} envoyée au client !'
                     + (' (et par WhatsApp)' if wa is not None else ''))
    return redirect('invoicing:invoice_detail', pk=pk)


@login_required
def invoice_mark_paid(request, pk):
    """Marquer une facture comme payée"""
    store, limit = _invoicing_scope(request, 'invoicing.create')
    if store is None:
        return _deny(request)
    if request.method != 'POST':
        # Action qui modifie la facture : formulaire POST (protégé CSRF) obligatoire
        return redirect('invoicing:invoice_detail', pk=pk)

    invoice = get_object_or_404(_visible_invoices(store, limit), pk=pk)
    invoice.mark_as_paid()
    # Notifier le client du paiement confirmé
    if invoice.customer:
        from messaging.utils import notify
        notify(
            invoice.customer, 'payment',
            f'Paiement confirmé — {invoice.invoice_number}',
            f'Votre paiement de {invoice.total_amount:.0f} FCFA a bien été reçu par {invoice.store.name}. Merci !',
            url='/commandes/',
            send_email=True,
        )
    messages.success(request, f'Facture {invoice.invoice_number} marquée comme payée !')
    return redirect('invoicing:invoice_detail', pk=pk)


@login_required
def invoice_settings_view(request):
    """Gérer les paramètres de facturation (propriétaire uniquement)"""
    store, _ = _invoicing_scope(request, None)
    if store is None:
        return _deny(request)
    settings_obj, _ = InvoiceSettings.objects.get_or_create(
        store=store,
        defaults={
            'company_name': store.name,
            'address': store.address,
            'city': store.city,
            'phone': store.phone,
            'email': store.email,
        }
    )

    if request.method == 'POST':
        settings_obj.company_name = request.POST.get('company_name', '')
        settings_obj.tax_id = request.POST.get('tax_id', '')
        settings_obj.registration_number = request.POST.get('registration_number', '')
        settings_obj.address = request.POST.get('address', '')
        settings_obj.city = request.POST.get('city', '')
        settings_obj.country = request.POST.get('country', 'Cameroun')
        settings_obj.phone = request.POST.get('phone', '')
        settings_obj.email = request.POST.get('email', '')
        settings_obj.website = request.POST.get('website', '')
        settings_obj.apply_tva = 'apply_tva' in request.POST
        settings_obj.tva_rate = request.POST.get('tva_rate', 19.25)
        settings_obj.invoice_prefix = request.POST.get('invoice_prefix', 'FAC')
        settings_obj.header_text = request.POST.get('header_text', '')
        settings_obj.footer_text = request.POST.get('footer_text', '')
        settings_obj.payment_terms = request.POST.get('payment_terms', '')
        settings_obj.bank_details = request.POST.get('bank_details', '')
        settings_obj.signature_name = request.POST.get('signature_name', '')
        settings_obj.signature_title = request.POST.get('signature_title', '')

        if request.FILES.get('logo'):
            settings_obj.logo = request.FILES['logo']
        if request.FILES.get('signature'):
            settings_obj.signature = request.FILES['signature']

        settings_obj.save()
        messages.success(request, 'Paramètres de facturation enregistrés !')
        return redirect('invoicing:settings')

    context = {
        'settings': settings_obj,
    }
    return render(request, 'invoicing/settings.html', context)
