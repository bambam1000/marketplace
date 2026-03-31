from django.contrib import admin
from .models import PromoCode, Campaign, Newsletter, MarketingAnalytics, LoyaltyProgram


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'store', 'discount_type', 'discount_value', 'usage_count', 'is_active', 'valid_to']
    list_filter = ['discount_type', 'is_active', 'store']
    search_fields = ['code', 'description']


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'store', 'campaign_type', 'status', 'start_date', 'end_date', 'conversions_count', 'revenue_generated']
    list_filter = ['campaign_type', 'status', 'store']
    search_fields = ['name', 'description']
    filter_horizontal = ['target_products']


@admin.register(Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ['subject', 'store', 'status', 'recipients_count', 'opened_count', 'sent_at']
    list_filter = ['status', 'store']
    search_fields = ['subject']


@admin.register(MarketingAnalytics)
class MarketingAnalyticsAdmin(admin.ModelAdmin):
    list_display = ['store', 'date', 'page_views', 'unique_visitors', 'purchases', 'revenue', 'conversion_rate']
    list_filter = ['store', 'date']
    date_hierarchy = 'date'


@admin.register(LoyaltyProgram)
class LoyaltyProgramAdmin(admin.ModelAdmin):
    list_display = ['user', 'store', 'points', 'tier', 'total_spent', 'total_orders']
    list_filter = ['tier', 'store']
    search_fields = ['user__username', 'user__email']
