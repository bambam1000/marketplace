"""Client HTTP minimal pour Evolution API v2 (mode Baileys).

Bibliothèque standard uniquement (urllib) : pas de dépendance à ajouter.
Toutes les méthodes lèvent EvolutionError en cas d'échec (réseau, clé refusée, réponse d'erreur).
"""
import json
from urllib import error, request
from urllib.parse import quote, urlencode

from django.conf import settings

# Événements reçus par le webhook Django (voir whatsapp.views.webhook)
WEBHOOK_EVENTS = ['CONNECTION_UPDATE', 'QRCODE_UPDATED', 'MESSAGES_UPSERT', 'MESSAGES_UPDATE', 'SEND_MESSAGE']


class EvolutionError(Exception):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class EvolutionClient:
    def __init__(self, base_url=None, api_key=None, timeout=None):
        self.base_url = (base_url or settings.EVOLUTION_API_URL).rstrip('/')
        self.api_key = api_key if api_key is not None else settings.EVOLUTION_API_KEY
        self.timeout = timeout or settings.WHATSAPP_HTTP_TIMEOUT

    # ── Transport ──
    def _request(self, method, path, payload=None, query=None):
        url = f'{self.base_url}{path}'
        if query:
            url += '?' + urlencode(query)
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = request.Request(url, data=body, method=method, headers={
            'apikey': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode('utf-8') or '{}'
                return json.loads(raw)
        except error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:500]
            try:
                parsed = json.loads(detail)
            except ValueError:
                parsed = None
            raise EvolutionError(f'Evolution API {exc.code} : {detail}', status=exc.code, payload=parsed) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise EvolutionError(f"Evolution API injoignable ({self.base_url}) : {exc}") from exc
        except ValueError as exc:
            raise EvolutionError(f"{self.base_url} ne répond pas en JSON : ce n'est probablement pas Evolution API "
                                 "(un autre programme utilise peut-être ce port).", status='not_evolution') from exc

    @staticmethod
    def _name(instance_name):
        return quote(instance_name, safe='')

    # ── Serveur ──
    def server_info(self):
        """Infos du serveur ; lève EvolutionError(status='not_evolution') si ce n'est pas Evolution API."""
        data = self._request('GET', '/')
        if 'Evolution API' not in str(data.get('message', '')):
            raise EvolutionError(f"{self.base_url} répond, mais ce n'est pas Evolution API.", status='not_evolution')
        return data

    def verify_key(self):
        """True si la clé API est acceptée."""
        try:
            self._request('POST', '/verify-creds', {})
            return True
        except EvolutionError as exc:
            if exc.status in (401, 403):
                return False
            raise

    # ── Instances ──
    @staticmethod
    def webhook_config(webhook_url, webhook_secret):
        return {
            'enabled': True,
            'url': webhook_url,
            'byEvents': False,
            'base64': False,
            'headers': {'X-AfriMarket-Secret': webhook_secret},
            'events': WEBHOOK_EVENTS,
        }

    def create_instance(self, instance_name, webhook_url, webhook_secret):
        return self._request('POST', '/instance/create', {
            'instanceName': instance_name,
            'integration': 'WHATSAPP-BAILEYS',
            'qrcode': True,
            'groupsIgnore': True,
            'rejectCall': False,
            'webhook': self.webhook_config(webhook_url, webhook_secret),
        })

    def set_webhook(self, instance_name, webhook_url, webhook_secret):
        """Met à jour URL, secret et événements d'une instance existante (ex. après une mise à jour du site)."""
        return self._request('POST', f'/webhook/set/{self._name(instance_name)}',
                             {'webhook': self.webhook_config(webhook_url, webhook_secret)})

    def connect(self, instance_name):
        """Démarre la connexion et renvoie le QR code : {'base64': 'data:image/png;base64,...', 'code': ...}."""
        return self._request('GET', f'/instance/connect/{self._name(instance_name)}')

    def connection_state(self, instance_name):
        """'open' (connecté), 'connecting' (QR affiché) ou 'close'."""
        data = self._request('GET', f'/instance/connectionState/{self._name(instance_name)}')
        return (data.get('instance') or {}).get('state') or 'close'

    def fetch_instance(self, instance_name):
        data = self._request('GET', '/instance/fetchInstances', query={'instanceName': instance_name})
        return data[0] if isinstance(data, list) and data else None

    def logout(self, instance_name):
        return self._request('DELETE', f'/instance/logout/{self._name(instance_name)}')

    def delete_instance(self, instance_name):
        return self._request('DELETE', f'/instance/delete/{self._name(instance_name)}')

    # ── Messages ──
    def check_numbers(self, instance_name, numbers):
        """{numéro: True/False} selon que le numéro a un compte WhatsApp."""
        data = self._request('POST', f'/chat/whatsappNumbers/{self._name(instance_name)}', {'numbers': list(numbers)})
        result = {}
        for row in data if isinstance(data, list) else []:
            number = str(row.get('number', '')).lstrip('+')
            result[number] = bool(row.get('exists'))
        return result

    def send_text(self, instance_name, number, text, delay_ms=None):
        return self._request('POST', f'/message/sendText/{self._name(instance_name)}', {
            'number': number,
            'text': text,
            'delay': delay_ms if delay_ms is not None else settings.WHATSAPP_SEND_DELAY_MS,
            'linkPreview': False,
        })

    def send_document(self, instance_name, number, base64_data, file_name, caption='',
                      mimetype='application/pdf', delay_ms=None):
        return self._request('POST', f'/message/sendMedia/{self._name(instance_name)}', {
            'number': number,
            'mediatype': 'document',
            'mimetype': mimetype,
            'media': base64_data,
            'fileName': file_name,
            'caption': caption,
            'delay': delay_ms if delay_ms is not None else settings.WHATSAPP_SEND_DELAY_MS,
        })

    @staticmethod
    def message_id(response):
        """Identifiant WhatsApp du message envoyé (sert à suivre les accusés reçu / lu)."""
        return ((response or {}).get('key') or {}).get('id', '')
