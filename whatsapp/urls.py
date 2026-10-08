from django.urls import path

from . import views

app_name = 'whatsapp'

urlpatterns = [
    path('', views.whatsapp_settings, name='settings'),
    path('connecter/', views.whatsapp_connect, name='connect'),
    path('statut/', views.whatsapp_status, name='status'),
    path('deconnecter/', views.whatsapp_disconnect, name='disconnect'),
    path('test/', views.whatsapp_test, name='test'),
    path('messages/', views.inbox, name='inbox'),
    path('messages/<int:pk>/', views.chat_detail, name='chat'),
    path('messages/<int:pk>/nouveaux/', views.chat_updates, name='chat_updates'),
]
