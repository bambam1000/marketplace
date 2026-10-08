from django.contrib import admin

from .models import WhatsAppInstance, WhatsAppMessage


@admin.register(WhatsAppInstance)
class WhatsAppInstanceAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'status', 'phone', 'connected_at')
    list_filter = ('status',)
    search_fields = ('name', 'store__name', 'phone')


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'purpose', 'to_number', 'status', 'attempts', 'instance')
    list_filter = ('status', 'purpose')
    search_fields = ('to_number', 'message_id', 'order__order_number')
    readonly_fields = ('message_id', 'error', 'attempts', 'sent_at')
