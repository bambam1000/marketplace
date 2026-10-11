from django.urls import path

from . import online_views as views

app_name = 'payments'

urlpatterns = [
    path('reglages/', views.gateway_settings, name='settings'),
    path('reglages/<str:provider>/', views.gateway_settings, name='settings_provider'),
    path('retour/', views.pay_return, name='return'),
]
