from django.urls import path
from . import views

app_name = 'marketing'

urlpatterns = [
    # Dashboard
    path('', views.marketing_dashboard, name='dashboard'),

    # Codes promo
    path('promo-codes/', views.promo_codes_list, name='promo_codes'),
    path('promo-codes/create/', views.promo_code_create, name='promo_code_create'),
    path('promo-codes/<int:pk>/edit/', views.promo_code_edit, name='promo_code_edit'),
    path('promo-codes/<int:pk>/toggle/', views.promo_code_toggle, name='promo_code_toggle'),

    # Campagnes
    path('campaigns/', views.campaigns_list, name='campaigns'),
    path('campaigns/create/', views.campaign_create, name='campaign_create'),
    path('campaigns/<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:pk>/edit/', views.campaign_edit, name='campaign_edit'),

    # Analytics
    path('analytics/', views.analytics, name='analytics'),

    # Programme de fidélité
    path('loyalty/', views.loyalty_program, name='loyalty'),
]
