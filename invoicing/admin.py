from django.contrib import admin
from .models import InvoiceSettings, Invoice, InvoiceItem, DeliveryNote, PaymentReceipt


@admin.register(InvoiceSettings)
class InvoiceSettingsAdmin(admin.ModelAdmin):
    list_display = ['store', 'company_name', 'tax_id', 'apply_tva', 'tva_rate']
    list_filter = ['apply_tva']
    search_fields = ['company_name', 'tax_id']


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1
    fields = ['description', 'quantity', 'unit_price', 'total']
    readonly_fields = ['total']


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'store', 'customer_name', 'invoice_type', 'status', 'total_amount', 'issue_date']
    list_filter = ['invoice_type', 'status', 'store', 'issue_date']
    search_fields = ['invoice_number', 'customer_name', 'customer_email']
    readonly_fields = ['invoice_number', 'uuid', 'subtotal', 'tva_amount', 'total_amount']
    inlines = [InvoiceItemInline]
    date_hierarchy = 'issue_date'


@admin.register(DeliveryNote)
class DeliveryNoteAdmin(admin.ModelAdmin):
    list_display = ['delivery_number', 'invoice', 'order', 'delivery_date']
    list_filter = ['delivery_date']
    search_fields = ['delivery_number']


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ['receipt_number', 'invoice', 'amount_paid', 'payment_date', 'payment_method']
    list_filter = ['payment_date', 'payment_method']
    search_fields = ['receipt_number', 'transaction_reference']
