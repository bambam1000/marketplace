from django.urls import path
from . import views
app_name = 'billing'
urlpatterns = [
    path('plans/', views.plans_view, name='plans'),
    path('souscrire/<slug:plan_slug>/', views.subscribe_view, name='subscribe'),
    path('mon-abonnement/', views.my_subscription, name='my_subscription'),
    path('paiements/', views.payment_config, name='payment_config'),
    path('boost/<int:product_id>/', views.boost_product, name='boost_product'),
    path('mes-boosts/', views.my_boosts, name='my_boosts'),
]
