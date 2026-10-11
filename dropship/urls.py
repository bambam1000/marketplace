from django.urls import path

from . import views

app_name = 'dropship'

urlpatterns = [
    path('', views.index, name='index'),
    path('guide/', views.guide, name='guide'),
    path('<int:pk>/', views.products, name='home'),
    path('<int:pk>/produits/', views.products, name='products'),
    path('<int:pk>/catalogue/', views.cj_catalog, name='cj_catalog'),
    path('<int:pk>/reglages/', views.settings_view, name='settings'),
    path('<int:pk>/import/', views.import_upload, name='import'),
    path('<int:pk>/import/modele/', views.import_template, name='import_template'),
    path('<int:pk>/import/<int:imp>/', views.import_map, name='import_map'),
    path('<int:pk>/import/<int:imp>/resultat/', views.import_result, name='import_result'),
    path('<int:pk>/images/', views.fetch_images, name='fetch_images'),
    path('<int:pk>/commandes/', views.orders, name='orders'),
    path('<int:pk>/commandes/<int:so>/', views.order_detail, name='order_detail'),
    path('<int:pk>/commandes/<int:so>/bon/', views.order_sheet, name='order_sheet'),
]
