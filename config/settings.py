import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Sécurité : les valeurs sensibles viennent des variables d'environnement.
# En développement, des valeurs par défaut sont utilisées.
# En production, définissez DJANGO_SECRET_KEY, DJANGO_DEBUG=False et DJANGO_ALLOWED_HOSTS.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-alibaba-marketplace-key-2026')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'accounts',
    'catalog',
    'store',
    'cart',
    'orders',
    'messaging',
    'dashboard',
    'billing',
    'accounting',
    'marketing',
    'invoicing',
    'pos',
    'inventory',
    'whatsapp',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'cart.context_processors.cart_context',
                'catalog.context_processors.categories_context',
                'messaging.context_processors.unread_messages_count',
                'messaging.context_processors.notifications_context',
                'dashboard.context_processors.dash_nav',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # DJANGO_DB_PATH permet aux scripts de test de travailler sur une copie de la base
        'NAME': os.environ.get('DJANGO_DB_PATH') or BASE_DIR / 'db.sqlite3',
    }
}

AUTH_USER_MODEL = 'accounts.User'

LANGUAGE_CODE = 'fr'
TIME_ZONE = 'Africa/Douala'
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ('fr', 'Français'),
    ('en', 'English'),
]

LOCALE_PATHS = [
    BASE_DIR / 'locale',
]

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
# DJANGO_MEDIA_ROOT permet aux scripts de test d'écrire leurs fichiers ailleurs que dans media/
MEDIA_ROOT = os.environ.get('DJANGO_MEDIA_ROOT') or BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/compte/connexion/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# Boost System Configuration
# ── Assistant IA (Claude, API Anthropic) — voir dashboard/ai_assistant.py ──
# Activé dès qu'une clé est fournie (ANTHROPIC_API_KEY), ou forcé avec ASSISTANT_AI_ENABLED=1 (profil `ant auth login`).
# Sans clé, l'ancien assistant par mots-clés reste utilisé.
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ASSISTANT_AI_ENABLED = os.environ.get('ASSISTANT_AI_ENABLED', '1' if ANTHROPIC_API_KEY else '0') == '1'
ASSISTANT_MODEL = os.environ.get('ASSISTANT_MODEL', 'claude-opus-5-5')
ASSISTANT_EFFORT = os.environ.get('ASSISTANT_EFFORT', 'medium')        # low | medium | high | xhigh | max
ASSISTANT_MAX_TOOL_ROUNDS = int(os.environ.get('ASSISTANT_MAX_TOOL_ROUNDS', '8'))
ASSISTANT_TIMEOUT = float(os.environ.get('ASSISTANT_TIMEOUT', '120'))

SALES_COMMISSION_RATE = 0.10  # commission plateforme sur les ventes en ligne (pas sur les ventes directes)
BOOST_COMMISSION_RATE = 0.20  # 20% commission for Facebook/WhatsApp boosts

# Email Configuration
# En développement : console. En production : définir DJANGO_EMAIL_BACKEND=smtp et les variables SMTP.
if os.environ.get('DJANGO_EMAIL_BACKEND', 'console') == 'smtp':
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'AfriMarket <noreply@afrimarket.com>')
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000')

# ── WhatsApp via Evolution API (mode Baileys) — voir deploy/evolution/README.md ──
# Désactivé par défaut : aucun appel réseau tant que WHATSAPP_ENABLED=1 et EVOLUTION_API_KEY ne sont pas définis.
def _local_evolution_env():
    """En développement (DEBUG) : lit deploy/evolution/.env pour que « manage.py runserver » suffise.
    Les variables d'environnement restent prioritaires ; en production ce fichier n'est jamais lu."""
    path = BASE_DIR / 'deploy' / 'evolution' / '.env'
    if not DEBUG or not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


_EVOLUTION_LOCAL = _local_evolution_env()
WHATSAPP_ENABLED = os.environ.get('WHATSAPP_ENABLED', '1' if _EVOLUTION_LOCAL else '0') == '1'
EVOLUTION_API_URL = os.environ.get('EVOLUTION_API_URL', 'http://127.0.0.1:8081')
EVOLUTION_API_KEY = os.environ.get('EVOLUTION_API_KEY', _EVOLUTION_LOCAL.get('AUTHENTICATION_API_KEY', ''))
WHATSAPP_WEBHOOK_SECRET = os.environ.get('WHATSAPP_WEBHOOK_SECRET', _EVOLUTION_LOCAL.get('AFRIMARKET_WEBHOOK_SECRET', ''))
# En local, le conteneur Docker joint Django sur la machine via host.docker.internal
WHATSAPP_WEBHOOK_URL = os.environ.get('WHATSAPP_WEBHOOK_URL', (
    'http://host.docker.internal:8000/whatsapp/webhook/' if _EVOLUTION_LOCAL
    else SITE_URL.rstrip('/') + '/whatsapp/webhook/'))
if _EVOLUTION_LOCAL and 'host.docker.internal' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('host.docker.internal')
WHATSAPP_PLATFORM_INSTANCE = os.environ.get('WHATSAPP_PLATFORM_INSTANCE', 'afrimarket')
WHATSAPP_DEFAULT_COUNTRY_CODE = os.environ.get('WHATSAPP_DEFAULT_COUNTRY_CODE', '237')
WHATSAPP_SEND_DELAY_MS = int(os.environ.get('WHATSAPP_SEND_DELAY_MS', '1200'))  # « en train d'écrire » avant envoi
WHATSAPP_HTTP_TIMEOUT = int(os.environ.get('WHATSAPP_HTTP_TIMEOUT', '15'))
# Qui envoie les messages en file : 'thread' (fil d'arrière-plan, défaut), 'worker' (manage.py whatsapp_worker,
# recommandé en production), 'inline' (dans la requête, pour les tests)
WHATSAPP_DELIVERY_MODE = os.environ.get('WHATSAPP_DELIVERY_MODE', 'thread')

# Sécurité renforcée en production (DEBUG=False)
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
