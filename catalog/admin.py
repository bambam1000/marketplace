from django.contrib import admin
from .models import Category, Product, Review, Wishlist, FlashDeal, ContactMessage

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'slug', 'icon', 'order']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'store', 'category', 'price', 'stock', 'is_active', 'is_featured', 'is_flash_deal']
    list_filter = ['category', 'is_active', 'is_featured', 'is_flash_deal', 'store']
    search_fields = ['name']

admin.site.register(Review)
admin.site.register(Wishlist)
admin.site.register(FlashDeal)

@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'subject', 'is_read', 'created_at']
    list_filter = ['is_read', 'created_at']
    search_fields = ['name', 'email', 'subject', 'message']
    readonly_fields = ['name', 'email', 'phone', 'subject', 'message', 'created_at']
    list_per_page = 50
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False
