from django.urls import path
from . import views

app_name = 'pos'

urlpatterns = [
    path('', views.pos_dashboard, name='dashboard'),
    path('caisses/', views.registers, name='registers'),
    path('caisses/<int:pk>/', views.register_edit, name='register_edit'),

    # Sessions
    path('open-session/', views.open_session, name='open_session'),
    path('close-session/<int:session_id>/', views.close_session, name='close_session'),
    path('sessions/', views.sessions_list, name='sessions'),
    path('sessions/<int:session_id>/', views.session_detail, name='session_detail'),
    path('sessions/<int:session_id>/rapport/', views.session_report, name='session_report'),
    path('sessions/<int:session_id>/mouvement/', views.cash_movement, name='cash_movement'),
    path('interface/', views.pos_interface, name='interface'),

    # API endpoints
    path('api/add-item/', views.add_item, name='add_item'),
    path('api/update-item/<int:item_id>/', views.update_item, name='update_item'),
    path('api/remove-item/<int:item_id>/', views.remove_item, name='remove_item'),
    path('api/discount/<int:sale_id>/', views.apply_discount, name='apply_discount'),
    path('api/clear/<int:sale_id>/', views.clear_cart, name='clear_cart'),
    path('api/hold/<int:sale_id>/', views.hold_sale, name='hold_sale'),
    path('api/resume/<int:sale_id>/', views.resume_sale, name='resume_sale'),
    path('api/complete/<int:sale_id>/', views.complete_sale, name='complete_sale'),
    path('api/search/', views.search_products, name='search_products'),
    path('api/refund/<int:sale_id>/', views.refund_sale, name='refund_sale'),

    # Ventes et impressions
    path('ventes/<int:sale_id>/', views.sale_detail, name='sale_detail'),
    path('receipt/<int:sale_id>/', views.print_receipt, name='print_receipt'),

    # Rapports
    path('reports/', views.sales_report, name='sales_report'),
]
