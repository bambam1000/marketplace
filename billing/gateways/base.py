"""Interface commune des prestataires de paiement."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal

TIMEOUT = 20


class GatewayError(Exception):
    """Erreur lisible par le vendeur ou le client."""
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class PaymentResult:
    """État d'un paiement chez le prestataire, normalisé."""
    status: str                 # pending, paid, failed, cancelled, expired
    amount: Decimal = None
    currency: str = ''
    provider_ref: str = ''
    channel: str = ''
    raw: dict = field(default_factory=dict)


def transport(method, url, headers, body):
    """Appel HTTP brut → (statut, octets). Remplacé dans les tests."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(1_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200_000)


def call(method, url, headers, payload=None, form=None):
    """(statut HTTP, JSON) ; GatewayError si le prestataire est injoignable ou répond n'importe quoi.
    payload : corps JSON ; form : corps x-www-form-urlencoded (liste de paires, pour Stripe)."""
    import urllib.parse
    from . import base
    if form is not None:
        body, ctype = urllib.parse.urlencode(form).encode(), 'application/x-www-form-urlencoded'
    else:
        body, ctype = (json.dumps(payload).encode() if payload is not None else None), 'application/json'
    try:
        status, raw = base.transport(method, url, {'Accept': 'application/json', 'Content-Type': ctype, **headers}, body)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GatewayError(f'Le service de paiement ne répond pas ({getattr(exc, "reason", exc)}). Réessayez dans un instant.')
    try:
        return status, json.loads(raw or b'{}')
    except ValueError:
        raise GatewayError(f'Réponse illisible du service de paiement (HTTP {status}).', status)


class Gateway:
    label = ''

    def __init__(self, gateway):
        self.gateway = gateway

    def check_keys(self, public_key):
        """Vérifie une clé avant de l'enregistrer. Lève GatewayError si elle est refusée."""
        raise NotImplementedError

    # Montant minimum accepté (paiement d'essai)
    test_amount = 100

    def create(self, payment, customer, callback_url, description, cancel_url=''):
        """Crée le paiement ; renvoie (adresse de la page de paiement, référence prestataire)."""
        raise NotImplementedError

    def retrieve(self, payment):
        """État actuel du paiement chez le prestataire (PaymentResult)."""
        raise NotImplementedError

    def verify_webhook(self, raw_body, headers):
        """True si la notification est authentique."""
        raise NotImplementedError

    @staticmethod
    def webhook_references(payload):
        """Références candidates contenues dans une notification."""
        raise NotImplementedError
