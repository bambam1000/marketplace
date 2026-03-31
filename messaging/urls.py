from django.urls import path
from . import views
app_name = 'messaging'
urlpatterns = [
    path('', views.inbox, name='inbox'),
    path('conversation/<int:pk>/', views.conversation, name='conversation'),
    path('nouveau/<int:seller_id>/', views.new_conversation, name='new'),

    # API endpoints
    path('api/unread-count/', views.get_unread_count, name='unread_count'),
    path('api/conversation/<int:conversation_id>/messages/', views.get_new_messages, name='new_messages'),
]
