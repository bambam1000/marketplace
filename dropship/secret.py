"""Chiffrement des clés d'API fournisseur stockées en base.

Flux HMAC-SHA256 en mode compteur (clé dérivée de SECRET_KEY), nonce aléatoire et tag HMAC :
une base copiée sans le SECRET_KEY ne livre pas les clés. Si SECRET_KEY change, `decrypt`
renvoie '' et le vendeur doit simplement ressaisir sa clé.
"""
import base64
import hashlib
import hmac
import os

from django.conf import settings

PREFIX = 'v1:'


def _keys():
    base = settings.SECRET_KEY.encode()
    return (hmac.new(base, b'dropship-enc', hashlib.sha256).digest(),
            hmac.new(base, b'dropship-mac', hashlib.sha256).digest())


def _stream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, 'big'), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(text):
    if not text:
        return ''
    enc, mac = _keys()
    data = text.encode()
    nonce = os.urandom(16)
    body = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
    tag = hmac.new(mac, nonce + body, hashlib.sha256).digest()[:16]
    return PREFIX + base64.urlsafe_b64encode(nonce + tag + body).decode()


def decrypt(token):
    if not token or not token.startswith(PREFIX):
        return ''
    try:
        raw = base64.urlsafe_b64decode(token[len(PREFIX):].encode())
    except (ValueError, TypeError):
        return ''
    nonce, tag, body = raw[:16], raw[16:32], raw[32:]
    enc, mac = _keys()
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + body, hashlib.sha256).digest()[:16]):
        return ''
    try:
        return bytes(a ^ b for a, b in zip(body, _stream(enc, nonce, len(body)))).decode()
    except UnicodeDecodeError:
        return ''
