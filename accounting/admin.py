from django.contrib import admin
from .models import Transaction, SellerWallet, PayoutRequest, Expense, PlatformStats

@admin.register(Transaction)
class TxnAdmin(admin.ModelAdmin):
    list_display = ['transaction_id', 'user', 'type', 'amount', 'status', 'created_at']
    list_filter = ['type', 'status']

@admin.register(SellerWallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ['user', 'balance', 'pending_balance', 'total_earned']

@admin.register(PayoutRequest)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'payment_method', 'status', 'created_at']

admin.site.register(Expense)
admin.site.register(PlatformStats)
