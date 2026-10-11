"""Prestataires de paiement en ligne. Chacun expose la même interface (voir base.py) ;
en ajouter un = écrire son module et l'inscrire dans PROVIDERS."""
from .base import GatewayError, PaymentResult  # noqa: F401
from .notchpay import NotchPay
from .stripe import Stripe

PROVIDERS = {'notchpay': NotchPay, 'stripe': Stripe}


def client_for(gateway):
    return PROVIDERS[gateway.provider](gateway)
