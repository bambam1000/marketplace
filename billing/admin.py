from django.contrib import admin
from .models import SubscriptionPlan, Subscription, SubscriptionPayment, PaymentConfig, ProductBoost

@admin.register(SubscriptionPlan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'price_monthly', 'max_products', 'commission_rate', 'badge_level', 'is_active', 'is_popular']

@admin.register(Subscription)
class SubAdmin(admin.ModelAdmin):
    list_display = ['user', 'plan', 'status', 'billing_cycle', 'end_date']

admin.site.register(SubscriptionPayment)
admin.site.register(PaymentConfig)
admin.site.register(ProductBoost)
