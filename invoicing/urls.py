from django.urls import path
from . import views

app_name = 'invoicing'

urlpatterns = [
    # Dashboard
    path('', views.invoices_dashboard, name='dashboard'),

    # Factures
    path('invoices/', views.invoices_list, name='invoices'),
    path('invoices/create/', views.invoice_create, name='invoice_create'),
    path('invoices/from-order/<str:order_number>/', views.invoice_from_order, name='invoice_from_order'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:pk>/edit/', views.invoice_edit, name='invoice_edit'),
    path('invoices/<int:pk>/pdf/', views.invoice_generate_pdf, name='invoice_pdf'),
    path('invoices/<int:pk>/send/', views.invoice_send, name='invoice_send'),
    path('invoices/<int:pk>/mark-paid/', views.invoice_mark_paid, name='invoice_mark_paid'),

    # Paramètres
    path('settings/', views.invoice_settings_view, name='settings'),
]
