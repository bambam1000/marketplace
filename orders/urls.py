from django.urls import path
from . import views
app_name = 'orders'
urlpatterns = [
    path('', views.order_list, name='list'),
    path('<str:order_number>/', views.order_detail, name='detail'),
    path('succes/<str:order_number>/', views.order_success, name='success'),

    # RFQ URLs
    path('rfq/nouveau/', views.create_rfq, name='create_rfq'),
    path('rfq/liste/', views.rfq_list, name='rfq_list'),
    path('rfq/<int:rfq_id>/', views.rfq_detail, name='rfq_detail'),
    path('rfq/mes-demandes/', views.my_rfqs, name='my_rfqs'),
    path('rfq/<int:rfq_id>/devis/', views.submit_quote, name='submit_quote'),
    path('devis/mes-devis/', views.my_quotes, name='my_quotes'),
    path('devis/<int:quote_id>/accepter/', views.accept_quote, name='accept_quote'),
    path('devis/<int:quote_id>/rejeter/', views.reject_quote, name='reject_quote'),
]
