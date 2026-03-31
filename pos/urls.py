from django.urls import path
from . import views

app_name = 'pos'

urlpatterns = [
    path('', views.pos_dashboard, name='dashboard'),
    path('open-session/', views.open_session, name='open_session'),
    path('close-session/<int:session_id>/', views.close_session, name='close_session'),
    path('interface/', views.pos_interface, name='interface'),

    # API endpoints
    path('api/add-item/', views.add_item, name='add_item'),
    path('api/update-item/<int:item_id>/', views.update_item, name='update_item'),
    path('api/remove-item/<int:item_id>/', views.remove_item, name='remove_item'),
    path('api/discount/<int:sale_id>/', views.apply_discount, name='apply_discount'),
    path('api/complete/<int:sale_id>/', views.complete_sale, name='complete_sale'),
    path('api/search/', views.search_products, name='search_products'),
    path('api/refund/<int:sale_id>/', views.refund_sale, name='refund_sale'),

    # Impressions
    path('receipt/<int:sale_id>/', views.print_receipt, name='print_receipt'),

    # Rapports
    path('reports/', views.sales_report, name='sales_report'),
]
