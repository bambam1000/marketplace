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

    # Notifications
    path('notifications/', views.notifications_list, name='notifications'),
    path('notifications/<int:pk>/lue/', views.notification_mark_read, name='notification_read'),
    path('notifications/toutes-lues/', views.notifications_mark_all_read, name='notifications_read_all'),
    path('notifications/<int:pk>/action/', views.notification_action, name='notification_action'),
    path('notifications/supprimer-lues/', views.notifications_delete_read, name='notifications_delete_read'),
    path('api/notifications/', views.notifications_feed, name='notifications_feed'),
    path('notifications/preferences/', views.notification_preferences, name='notification_preferences'),
]
