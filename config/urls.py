from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
]

urlpatterns += i18n_patterns(
    path('admin/', admin.site.urls),
    path('compte/', include('accounts.urls')),
    path('boutique/', include('catalog.urls')),
    path('vendeur/', include('store.urls')),
    path('panier/', include('cart.urls')),
    path('commandes/', include('orders.urls')),
    path('messages/', include('messaging.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('finance/', include('finance.urls')),
    path('abonnement/', include('billing.urls')),
    path('comptabilite/', include('accounting.urls')),
    path('marketing/', include('marketing.urls')),
    path('facturation/', include('invoicing.urls')),
    path('pos/', include('pos.urls')),
    path('', include('catalog.home_urls')),
)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
