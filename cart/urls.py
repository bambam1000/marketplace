from django.urls import path
from . import views
app_name = 'cart'
urlpatterns = [
    path('', views.cart_view, name='view'),
    path('ajouter/<int:product_id>/', views.add_to_cart, name='add'),
    path('modifier/', views.update_cart, name='update'),
    path('commander/', views.checkout, name='checkout'),
]
