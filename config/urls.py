from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns

from whatsapp import views as whatsapp_views
from config import pwa

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
    # Appelé par Evolution API (serveur à serveur) : hors préfixe de langue
    path('whatsapp/webhook/', whatsapp_views.webhook, name='whatsapp_webhook'),
    # Application installable (PWA) : à la racine, hors préfixe de langue
    path('manifest.webmanifest', pwa.manifest, name='pwa_manifest'),
    path('sw.js', pwa.service_worker, name='pwa_sw'),
    path('offline/', pwa.offline, name='pwa_offline'),
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
    path('dashboard/whatsapp/', include('whatsapp.urls')),
    path('', include('catalog.home_urls')),
)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
