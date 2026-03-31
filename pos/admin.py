from django.contrib import admin
from .models import POSSession, POSSale, POSSaleItem, CashMovement, POSProduct


@admin.register(POSSession)
class POSSessionAdmin(admin.ModelAdmin):
    list_display = ['session_number', 'store', 'cashier', 'status', 'total_sales', 'sales_count', 'opened_at']
    list_filter = ['status', 'store', 'opened_at']
    search_fields = ['session_number', 'cashier__email']
    readonly_fields = ['session_number', 'total_sales', 'total_cash', 'total_card', 'total_mobile_money', 'sales_count']


class POSSaleItemInline(admin.TabularInline):
    model = POSSaleItem
    extra = 0
    readonly_fields = ['total']


@admin.register(POSSale)
class POSSaleAdmin(admin.ModelAdmin):
    list_display = ['sale_number', 'session', 'customer_name', 'total_amount', 'status', 'created_at']
    list_filter = ['status', 'store', 'created_at']
    search_fields = ['sale_number', 'customer_name', 'customer_phone']
    readonly_fields = ['sale_number', 'subtotal', 'tax_amount', 'total_amount']
    inlines = [POSSaleItemInline]


@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = ['session', 'type', 'category', 'amount', 'description', 'created_at']
    list_filter = ['type', 'category', 'created_at']
    search_fields = ['description', 'reference']


@admin.register(POSProduct)
class POSProductAdmin(admin.ModelAdmin):
    list_display = ['product', 'barcode', 'quick_button', 'button_order', 'sell_by_weight']
    list_filter = ['quick_button', 'sell_by_weight']
    search_fields = ['product__name', 'barcode']
