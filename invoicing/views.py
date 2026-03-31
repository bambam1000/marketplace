from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, FileResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.core.files.base import ContentFile
from datetime import timedelta
from .models import Invoice, InvoiceItem, InvoiceSettings, DeliveryNote, PaymentReceipt
from .pdf_generator import generate_invoice_pdf, generate_delivery_note_pdf, generate_payment_receipt_pdf
from orders.models import Order


@login_required
def invoices_dashboard(request):
    """Dashboard des factures"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        messages.error(request, 'Accès réservé aux vendeurs.')
        return redirect('dashboard:index')

    store = request.user.store
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
    invoices = Invoice.objects.filter(store=store)
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
    """Liste des factures"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    invoices = Invoice.objects.filter(store=store).select_related('customer', 'order')

    # Filtres
    status = request.GET.get('status')
    if status:
        invoices = invoices.filter(status=status)

    invoice_type = request.GET.get('type')
    if invoice_type:
        invoices = invoices.filter(invoice_type=invoice_type)

    search = request.GET.get('q', '')
    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search) |
            Q(customer_name__icontains=search) |
            Q(customer_email__icontains=search)
        )

    invoices = invoices.order_by('-created_at')

    context = {
        'invoices': invoices,
        'status_filter': status,
        'type_filter': invoice_type,
        'search': search,
    }
    return render(request, 'invoicing/invoices_list.html', context)


@login_required
def invoice_create(request):
    """Créer une facture manuelle"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    settings_obj, _ = InvoiceSettings.objects.get_or_create(store=store)

    if request.method == 'POST':
        # Créer la facture
        invoice = Invoice.objects.create(
            store=store,
            invoice_number=settings_obj.get_next_invoice_number(),
            invoice_type=request.POST.get('invoice_type', 'standard'),
            customer_name=request.POST.get('customer_name'),
            customer_email=request.POST.get('customer_email', ''),
            customer_phone=request.POST.get('customer_phone', ''),
            customer_address=request.POST.get('customer_address', ''),
            issue_date=request.POST.get('issue_date', timezone.now().date()),
            due_date=request.POST.get('due_date') or None,
            notes=request.POST.get('notes', ''),
            shipping_amount=request.POST.get('shipping_amount', 0),
            discount_amount=request.POST.get('discount_amount', 0),
            status='draft',
        )

        # Ajouter les articles
        descriptions = request.POST.getlist('item_description[]')
        quantities = request.POST.getlist('item_quantity[]')
        unit_prices = request.POST.getlist('item_unit_price[]')

        for i, desc in enumerate(descriptions):
            if desc.strip():
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=desc,
                    quantity=int(quantities[i]) if i < len(quantities) else 1,
                    unit_price=float(unit_prices[i]) if i < len(unit_prices) else 0,
                    order=i
                )

        invoice.calculate_totals()
        messages.success(request, f'Facture {invoice.invoice_number} créée !')
        return redirect('invoicing:invoice_detail', pk=invoice.pk)

    return render(request, 'invoicing/invoice_form.html')


@login_required
def invoice_from_order(request, order_number):
    """Créer une facture depuis une commande"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
    order = get_object_or_404(Order, order_number=order_number)
    settings_obj, _ = InvoiceSettings.objects.get_or_create(store=store)

    # Vérifier si une facture existe déjà
    existing = Invoice.objects.filter(order=order, store=store).first()
    if existing:
        messages.info(request, 'Une facture existe déjà pour cette commande.')
        return redirect('invoicing:invoice_detail', pk=existing.pk)

    # Créer la facture
    invoice = Invoice.objects.create(
        store=store,
        invoice_number=settings_obj.get_next_invoice_number(),
        order=order,
        customer=order.buyer,
        customer_name=order.buyer.get_full_name() or order.buyer.username,
        customer_email=order.buyer.email,
        customer_phone=getattr(order.buyer, 'phone', ''),
        customer_address=order.shipping_address or '',
        issue_date=timezone.now().date(),
        shipping_amount=order.shipping_fee or 0,
        status='draft' if not order.is_paid else 'paid',
    )

    # Ajouter les articles de la commande liés au vendeur
    for item in order.items.filter(store=store):
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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    invoice = get_object_or_404(Invoice, pk=pk, store=request.user.store)

    context = {
        'invoice': invoice,
    }
    return render(request, 'invoicing/invoice_detail.html', context)


@login_required
def invoice_edit(request, pk):
    """Modifier une facture"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    invoice = get_object_or_404(Invoice, pk=pk, store=request.user.store)

    if invoice.status == 'paid':
        messages.error(request, 'Impossible de modifier une facture payée.')
        return redirect('invoicing:invoice_detail', pk=pk)

    if request.method == 'POST':
        invoice.customer_name = request.POST.get('customer_name')
        invoice.customer_email = request.POST.get('customer_email', '')
        invoice.customer_phone = request.POST.get('customer_phone', '')
        invoice.customer_address = request.POST.get('customer_address', '')
        invoice.issue_date = request.POST.get('issue_date')
        invoice.due_date = request.POST.get('due_date') or None
        invoice.notes = request.POST.get('notes', '')
        invoice.shipping_amount = request.POST.get('shipping_amount', 0)
        invoice.discount_amount = request.POST.get('discount_amount', 0)
        invoice.save()

        # Supprimer les anciens articles
        invoice.items.all().delete()

        # Ajouter les nouveaux articles
        descriptions = request.POST.getlist('item_description[]')
        quantities = request.POST.getlist('item_quantity[]')
        unit_prices = request.POST.getlist('item_unit_price[]')

        for i, desc in enumerate(descriptions):
            if desc.strip():
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=desc,
                    quantity=int(quantities[i]) if i < len(quantities) else 1,
                    unit_price=float(unit_prices[i]) if i < len(unit_prices) else 0,
                    order=i
                )

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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    invoice = get_object_or_404(Invoice, pk=pk, store=request.user.store)

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
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    invoice = get_object_or_404(Invoice, pk=pk, store=request.user.store)

    # Générer le PDF si nécessaire
    if not invoice.pdf_file:
        pdf_buffer = generate_invoice_pdf(invoice)
        pdf_filename = f"facture_{invoice.invoice_number}.pdf"
        invoice.pdf_file.save(pdf_filename, ContentFile(pdf_buffer.read()), save=True)

    # TODO: Implémenter l'envoi par email
    # send_invoice_email(invoice)

    invoice.status = 'sent'
    invoice.sent_at = timezone.now()
    invoice.save()

    messages.success(request, f'Facture {invoice.invoice_number} marquée comme envoyée !')
    return redirect('invoicing:invoice_detail', pk=pk)


@login_required
def invoice_mark_paid(request, pk):
    """Marquer une facture comme payée"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    invoice = get_object_or_404(Invoice, pk=pk, store=request.user.store)
    invoice.mark_as_paid()
    messages.success(request, f'Facture {invoice.invoice_number} marquée comme payée !')
    return redirect('invoicing:invoice_detail', pk=pk)


@login_required
def invoice_settings_view(request):
    """Gérer les paramètres de facturation"""
    if not request.user.is_seller or not hasattr(request.user, 'store'):
        return redirect('dashboard:index')

    store = request.user.store
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
