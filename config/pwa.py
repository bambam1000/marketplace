"""Application installable (PWA) : manifeste, service worker et page hors ligne.

Ces adresses sont hors préfixe de langue : le service worker doit être servi depuis la racine
(/sw.js) pour contrôler tout le site, et le manifeste doit garder la même adresse dans toutes les langues.
"""
import json

from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control

# À incrémenter quand la liste des fichiers mis en cache change : les anciens caches sont supprimés.
CACHE_VERSION = 'comptoir-v3'


@cache_control(max_age=3600)
def manifest(request):
    icons = [
        {'src': static('images/pwa/icon-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
        {'src': static('images/pwa/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
        {'src': static('images/pwa/icon-maskable-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'maskable'},
        {'src': static('images/pwa/icon-maskable-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
    ]
    shortcut_icon = [{'src': static('images/pwa/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'}]
    data = {
        'id': '/',
        'name': 'Comptoir — Achetez et vendez en ligne',
        'short_name': 'Comptoir',
        'description': "La place de marché en ligne : boutiques indépendantes, vente au détail et en gros, paiement en ligne ou à la livraison.",
        'lang': 'fr',
        'dir': 'ltr',
        'start_url': '/?source=pwa',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#ffffff',
        'theme_color': '#ff6a00',
        'categories': ['shopping', 'business'],
        'icons': icons,
        'shortcuts': [
            {'name': 'Flash Deals', 'url': reverse('catalog:flash_deals'), 'icons': shortcut_icon},
            {'name': 'Mon panier', 'url': reverse('cart:view'), 'icons': shortcut_icon},
            {'name': 'Mes commandes', 'url': reverse('orders:list'), 'icons': shortcut_icon},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json; charset=utf-8', json_dumps_params={'ensure_ascii': False})


@cache_control(no_cache=True)
def service_worker(request):
    precache = [
        '/offline/',
        static('css/style.css'),
        static('css/mobile.css'),
        static('images/pwa/icon-192.png'),
        static('images/pwa/favicon-32.png'),
    ]
    response = render(request, 'pwa/sw.js', {'version': CACHE_VERSION, 'precache': json.dumps(precache)},
                      content_type='application/javascript; charset=utf-8')
    response['Service-Worker-Allowed'] = '/'
    return response


def offline(request):
    return render(request, 'pwa/offline.html')
