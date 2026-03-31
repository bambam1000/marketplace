from django.urls import path
from . import views
app_name = 'dashboard'
urlpatterns = [
    path('', views.index, name='index'),
    path('commandes/', views.dash_orders, name='orders'),
    path('commandes/<str:order_number>/', views.dash_order_detail, name='order_detail'),
    path('produits/', views.dash_products, name='products'),
    path('produits/ajouter/', views.dash_product_edit, name='product_add'),
    path('produits/<int:pk>/', views.dash_product_edit, name='product_edit'),
    path('clients/', views.dash_customers, name='customers'),
    path('boutique/', views.dash_store, name='store'),
    # Admin only
    path('utilisateurs/', views.admin_users, name='users'),
    path('utilisateurs/<int:pk>/', views.admin_user_detail, name='user_detail'),
    path('utilisateurs/<int:pk>/toggle/', views.admin_toggle_user, name='toggle_user'),
    path('vendeurs/', views.admin_sellers, name='sellers'),
    path('vendeurs/<int:pk>/verify/', views.admin_verify_seller, name='verify_seller'),
    path('versements/', views.admin_payouts, name='payouts'),
    path('versements/<int:pk>/process/', views.admin_process_payout, name='process_payout'),
    path('plateforme/', views.admin_platform_stats, name='platform_stats'),
    path('bannieres/', views.admin_banners, name='banners'),
    path('bannieres/ajouter/', views.admin_banner_edit, name='banner_add'),
    path('bannieres/<int:pk>/', views.admin_banner_edit, name='banner_edit'),
    path('bannieres/<int:pk>/supprimer/', views.admin_banner_delete, name='banner_delete'),
    path('categories/', views.categories, name='categories'),
    path('categories/ajouter/', views.category_add, name='category_add'),
    path('categories/<int:pk>/', views.category_edit, name='category_edit'),
    path('categories/<int:pk>/supprimer/', views.category_delete, name='category_delete'),
]
