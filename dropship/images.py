"""Téléchargement des images fournisseur (liens donnés dans le fichier importé).

Les liens viennent d'un fichier envoyé par le vendeur : seules les adresses publiques en http(s)
sont suivies (pas de réseau interne), la taille est plafonnée et le contenu doit être une vraie image.
"""
import io
import ipaddress
import socket
import time
import urllib.request
from urllib.parse import urlparse

from django.core.files.base import ContentFile

MAX_IMAGE_BYTES = 5 * 1024 * 1024
TIMEOUT = 8
IMAGE_FIELDS = ('image', 'image_2', 'image_3', 'image_4')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Chaque redirection est revérifiée comme le lien d'origine."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def is_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def fetch_image(url):
    """Contenu (octets, extension) d'une image, ou None."""
    if not is_public_url(url):
        return None
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Comptoir catalogue import)'})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
    except Exception:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        return None
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        fmt = (img.format or '').lower()
    except Exception:
        return None
    ext = {'jpeg': 'jpg', 'png': 'png', 'webp': 'webp', 'gif': 'gif'}.get(fmt)
    return (data, ext) if ext else None


def fetch_pending(items, budget_seconds=40):
    """Télécharge les images en attente des produits fournisseur donnés, dans la limite du temps accordé.
    Retourne (images enregistrées, produits restants)."""
    start, saved = time.monotonic(), 0
    items = list(items)
    for i, sp in enumerate(items):
        if time.monotonic() - start > budget_seconds:
            return saved, len(items) - i
        product = sp.product
        free = [f for f in IMAGE_FIELDS if not getattr(product, f)]
        changed = []
        for url in sp.image_urls:
            if not free:
                break
            got = fetch_image(url)
            if got:
                data, ext = got
                field = free.pop(0)
                getattr(product, field).save(f'{product.slug[:60]}-{field}.{ext}', ContentFile(data), save=False)
                changed.append(field)
                saved += 1
        if changed:
            product.save(update_fields=changed)
        sp.image_urls = []
        sp.save(update_fields=['image_urls'])
    return saved, 0
