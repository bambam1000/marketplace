"""Supprime l'historique de recommandation de plus de 90 jours. À lancer chaque jour (tâche planifiée)."""
from django.core.management.base import BaseCommand

from catalog.recommend import KEEP_DAYS, purge_old


class Command(BaseCommand):
    help = f"Efface les recherches et consultations de plus de {KEEP_DAYS} jours."

    def handle(self, *args, **options):
        self.stdout.write(f'{purge_old()} signal(aux) supprimé(s).')
