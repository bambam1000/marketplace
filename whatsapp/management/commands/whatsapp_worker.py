"""Processus d'envoi WhatsApp : remplace la tâche cron.

    python manage.py whatsapp_worker            # tourne en continu (service systemd / Docker / fenêtre dédiée)
    python manage.py whatsapp_worker --once     # un seul passage (tâche planifiée si on préfère)

À chaque passage : envoie les messages en file, relance les échecs dont le délai est écoulé,
débloque les envois interrompus, et vérifie toutes les minutes que les numéros sont toujours connectés.
"""
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from whatsapp import services


class Command(BaseCommand):
    help = "Envoie les messages WhatsApp en file et relance les échecs (processus continu)."

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Un seul passage puis arrêt')
        parser.add_argument('--interval', type=float, default=2.0, help='Secondes entre deux passages (défaut : 2)')

    def handle(self, *args, once=False, interval=2.0, **options):
        if not services.is_enabled():
            self.stdout.write('WhatsApp désactivé (WHATSAPP_ENABLED / EVOLUTION_API_KEY) : rien à faire.')
            return
        self._stop = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._request_stop)
            except (ValueError, OSError):  # hors du fil principal (tests)
                pass
        last_sync = 0.0
        self.stdout.write(self.style.SUCCESS('Worker WhatsApp démarré.'))
        while not self._stop:
            close_old_connections()
            try:
                if time.monotonic() - last_sync > 60:
                    services.sync_instances()
                    last_sync = time.monotonic()
                sent, total = services.process_queue()
                if total:
                    self.stdout.write(f'{sent}/{total} message(s) envoyé(s).')
            except Exception as exc:  # le worker ne doit jamais s'arrêter sur une erreur ponctuelle
                self.stderr.write(f'Erreur du worker : {exc}')
            if once:
                break
            time.sleep(interval)
        self.stdout.write('Worker WhatsApp arrêté.')

    def _request_stop(self, *args):
        self._stop = True
