from django.urls import path
from . import views
app_name = 'catalog'
urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('categorie/<slug:slug>/', views.category_view, name='category'),
    path('produit/<slug:slug>/', views.product_detail, name='product_detail'),
    path('recherche/', views.search_view, name='search'),
    path('api/search-autocomplete/', views.search_autocomplete, name='search_autocomplete'),
    path('flash-deals/', views.flash_deals, name='flash_deals'),
    path('promotions/', views.promotions, name='promotions'),
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', views.toggle_wishlist, name='toggle_wishlist'),
    path('avis/<int:product_id>/', views.add_review, name='add_review'),
]
