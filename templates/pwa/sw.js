/* Service worker AfriMarket — {{ version }}
   - pages : réseau d'abord, page « hors ligne » si pas de connexion ;
   - fichiers statiques, polices et images : servis depuis le cache puis rafraîchis en arrière-plan ;
   - jamais de cache pour l'espace vendeur, l'administration, les API ni les requêtes POST. */
const CACHE = '{{ version }}';
const OFFLINE_URL = '/offline/';
const PRECACHE = {{ precache|safe }};
const NEVER = [/\/admin\//, /\/dashboard\//, /\/pos\//, /\/api\//, /\/whatsapp\//, /\/compte\//, /\/panier\//, /\/commandes\//, /\/messages\//, /\/i18n\//];
const STATIC_HOSTS = ['cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'cdn.jsdelivr.net'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function staleWhileRevalidate(request) {
  return caches.open(CACHE).then((cache) => cache.match(request).then((cached) => {
    const network = fetch(request).then((response) => {
      if (response && (response.ok || response.type === 'opaque')) cache.put(request, response.clone());
      return response;
    }).catch(() => cached);
    return cached || network;
  }));
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    if (NEVER.some((re) => re.test(url.pathname))) {
      if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
      }
      return;
    }
    if (request.mode === 'navigate') {
      event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
      return;
    }
    if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/')) {
      event.respondWith(staleWhileRevalidate(request));
    }
    return;
  }

  if (STATIC_HOSTS.includes(url.hostname)) {
    event.respondWith(staleWhileRevalidate(request));
  }
});
