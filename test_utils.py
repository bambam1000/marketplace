# -*- coding: utf-8 -*-
"""Helpers partagés pour les suites de tests fonctionnels Comptoir.

Usage dans un script de test :
    from test_utils import make_checker, finish, cleanup_test_data
    results = []
    check = make_checker(results)
    ... tests ...
    sys.exit(1 if finish(results, 'Vendeur') else 0)
"""
import atexit
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def _use_isolated_db():
    """Fait tourner les tests sur une copie temporaire de db.sqlite3 et un dossier media temporaire,
    supprimés à la fin.

    La vraie base n'est jamais modifiée. Pour tester sur la vraie base (déconseillé) :
    TEST_USE_REAL_DB=1 python test_xxx.py
    """
    if os.environ.get('TEST_USE_REAL_DB') == '1' or os.environ.get('DJANGO_DB_PATH'):
        return
    source = Path(__file__).resolve().parent / 'db.sqlite3'
    fd, copy_path = tempfile.mkstemp(prefix='afrimarket_test_', suffix='.sqlite3')
    os.close(fd)
    # API de sauvegarde SQLite : copie cohérente même si le serveur de dev tourne
    src, dst = sqlite3.connect(source), sqlite3.connect(copy_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    os.environ['DJANGO_DB_PATH'] = copy_path
    # Fichiers envoyés pendant les tests (PDF, justificatifs...) : dossier temporaire, pas media/
    media_dir = tempfile.mkdtemp(prefix='afrimarket_test_media_')
    os.environ['DJANGO_MEDIA_ROOT'] = media_dir

    def _remove_copy():
        try:
            from django.db import connections
            connections.close_all()
        except Exception:
            pass
        try:
            os.remove(copy_path)
        except OSError:
            pass
        import shutil
        shutil.rmtree(media_dir, ignore_errors=True)
    atexit.register(_remove_copy)


_use_isolated_db()
os.environ.setdefault('WHATSAPP_ENABLED', '0')  # pas d'appel réseau pendant les tests (sauf si un test l'active)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'

import django
django.setup()

# La copie de test suit toujours le code : on lui applique les migrations en attente
if os.environ.get('TEST_USE_REAL_DB') != '1':
    from django.core.management import call_command
    call_command('migrate', verbosity=0)

# Console Windows (cp1252) : force l'UTF-8 sans crash
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from django.contrib.auth import get_user_model
User = get_user_model()


class _FakeResponse:
    """Réponse factice 500 quand une vue lève une exception (le test continue)."""
    status_code = 500
    url = ''
    context = None
    content = b''

    def json(self):
        return {}


def safe_get(client, url, **kwargs):
    """GET qui capture les exceptions de vue et retourne une réponse 500."""
    try:
        return client.get(url, **kwargs)
    except Exception as e:  # noqa: BLE001 — on veut tous les 500
        import traceback
        print(f'  !! EXCEPTION {type(e).__name__} sur GET {url} : {e}')
        traceback.print_exc()
        return _FakeResponse()


def safe_post(client, url, data=None, **kwargs):
    """POST qui capture les exceptions de vue et retourne une réponse 500."""
    try:
        return client.post(url, data or {}, **kwargs)
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f'  !! EXCEPTION {type(e).__name__} sur POST {url} : {e}')
        traceback.print_exc()
        return _FakeResponse()


def xlsx_bytes(rows):
    """Construit un fichier xlsx en mémoire (1 colonne, liste de valeurs)."""
    import io
    import openpyxl
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(buf)
    buf.seek(0)
    return SimpleUploadedFile(
        'import.xlsx', buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


def make_checker(results):
    """Retourne une fonction check(name, condition, extra='') qui journalise."""
    def check(name, condition, extra=''):
        results.append((name, bool(condition)))
        print(('PASS' if condition else 'FAIL'), '-', name, extra)
    return check


def finish(results, title):
    """Affiche le résumé et retourne le nombre d'échecs."""
    print()
    failed = [n for n, ok in results if not ok]
    print(f'RESULTAT [{title}] : {len(results) - len(failed)}/{len(results)} reussis')
    if failed:
        print('ECHECS :')
        for f in failed:
            print('  -', f)
    return len(failed)


def cleanup_test_data(suffix, promo_prefix=None, category_slugs=(), plan_slugs=(), banner_titles=()):
    """Supprime les données de test d'un run précédent (idempotence).

    Ordre important : les StockTransfer protègent leurs entrepôts (PROTECT),
    on les supprime donc avant la cascade utilisateurs -> boutiques -> entrepôts.
    """
    from inventory.models import StockTransfer
    from catalog.models import Category, HeroBanner
    from marketing.models import PromoCode
    from billing.models import SubscriptionPlan

    StockTransfer.objects.filter(store__owner__username__endswith=suffix).delete()
    User.objects.filter(username__endswith=suffix).delete()
    if category_slugs:
        Category.objects.filter(slug__in=list(category_slugs)).delete()
    if promo_prefix:
        PromoCode.objects.filter(code__startswith=promo_prefix).delete()
    if plan_slugs:
        SubscriptionPlan.objects.filter(slug__in=list(plan_slugs)).delete()
    if banner_titles:
        HeroBanner.objects.filter(title__in=list(banner_titles)).delete()
