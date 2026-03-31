from django.urls import path
from . import views
app_name = 'accounting'
urlpatterns = [
    path('', views.accounting_dashboard, name='dashboard'),
    path('transactions/', views.transactions_list, name='transactions'),
    path('portefeuille/', views.wallet_view, name='wallet'),
    path('retrait/', views.payout_request, name='payout_request'),
    path('depenses/', views.expenses_view, name='expenses'),
    path('rapport/', views.financial_report, name='report'),
]
