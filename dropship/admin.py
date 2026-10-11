from django.contrib import admin

from .models import SupplierConnection, SupplierImport, SupplierOrder, SupplierProduct


@admin.register(SupplierConnection)
class SupplierConnectionAdmin(admin.ModelAdmin):
    list_display = ['name', 'provider', 'warehouse', 'currency', 'markup', 'is_active']
    exclude = ['api_key_enc', 'access_token_enc', 'refresh_token_enc']
    readonly_fields = ['api_key_hint', 'token_expires_at', 'refresh_expires_at', 'api_connected_at', 'api_error']


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ['supplier_sku', 'product', 'connection', 'cost', 'supplier_stock']
    search_fields = ['supplier_sku', 'product__name']


@admin.register(SupplierOrder)
class SupplierOrderAdmin(admin.ModelAdmin):
    list_display = ['reference', 'order', 'connection', 'status', 'cost', 'tracking_number']
    list_filter = ['status']


admin.site.register(SupplierImport)
