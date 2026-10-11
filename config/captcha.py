"""Captcha image fait maison : aucune clé ni service externe, fonctionne aussi hors ligne.

Le code est gardé en session, par formulaire (« login », « contact »), et ne sert qu'une fois :
chaque vérification l'efface, réussie ou non, donc chaque essai demande une nouvelle image.
"""
import io
import random
import secrets
import time

from django.http import Http404, HttpResponse
from django.views.decorators.cache import never_cache

SCOPES = {'login', 'contact'}
# Sans les caractères faciles à confondre (0/O, 1/I/L)
ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
LENGTH = 5
TTL = 10 * 60
SESSION_KEY = 'captcha'


def _render(code):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    rnd = random.Random(secrets.randbits(64))
    w, h = 180, 56
    img = Image.new('RGB', (w, h), (255, 247, 240))
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        draw.line([(rnd.randint(0, w), rnd.randint(0, h)) for _ in range(2)],
                  fill=(rnd.randint(150, 220), rnd.randint(150, 210), rnd.randint(150, 210)), width=rnd.randint(1, 2))
    font = ImageFont.load_default(size=34)
    x = 12
    for ch in code:
        tile = Image.new('RGBA', (44, 52), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((8, 4), ch, font=font,
                                  fill=(rnd.randint(20, 120), rnd.randint(20, 80), rnd.randint(20, 90), 255))
        tile = tile.rotate(rnd.uniform(-28, 28), resample=Image.BICUBIC, expand=False)
        img.paste(tile, (x, rnd.randint(-2, 6)), tile)
        x += rnd.randint(28, 33)
    for _ in range(3):
        y = rnd.randint(14, h - 14)
        draw.line([(0, y), (w // 2, y + rnd.randint(-12, 12)), (w, y + rnd.randint(-10, 10))],
                  fill=(255, 106, 0), width=1)
    for _ in range(260):
        draw.point((rnd.randrange(w), rnd.randrange(h)),
                   fill=(rnd.randint(120, 230), rnd.randint(120, 230), rnd.randint(120, 230)))
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


@never_cache
def image(request, scope):
    """Nouvelle image à chaque appel (le bouton « autre image » recharge simplement l'URL)."""
    if scope not in SCOPES:
        raise Http404
    code = ''.join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    store = request.session.get(SESSION_KEY) or {}
    store[scope] = {'code': code, 'ts': int(time.time())}
    request.session[SESSION_KEY] = store
    return HttpResponse(_render(code), content_type='image/png')


def verify(request, scope, field='captcha'):
    """Vrai si le code saisi correspond à la dernière image affichée pour ce formulaire."""
    answer = (request.POST.get(field) or '').strip().upper().replace(' ', '')
    store = request.session.get(SESSION_KEY) or {}
    entry = store.pop(scope, None)
    request.session[SESSION_KEY] = store
    if not entry or not answer or time.time() - entry.get('ts', 0) > TTL:
        return False
    return secrets.compare_digest(answer, entry['code'])
