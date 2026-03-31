from django.urls import path
from . import views
app_name = 'store'
urlpatterns = [
    path('', views.store_list, name='list'),
    path('<slug:slug>/', views.store_detail, name='detail'),
]
