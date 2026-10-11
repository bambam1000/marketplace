from django.urls import path
from . import views
from dashboard import admin_views
app_name = 'accounting'
urlpatterns = [
    path('', views.accounting_dashboard, name='dashboard'),
    path('transactions/', views.transactions_list, name='transactions'),
    path('portefeuille/', views.wallet_view, name='wallet'),
    path('retrait/', views.payout_request, name='payout_request'),
    path('depenses/', admin_views.expenses, name='expenses'),
    path('rapport/', views.financial_report, name='report'),
]
