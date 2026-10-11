"""Télécharge les images encore en attente des produits fournisseur (à planifier, ex. toutes les heures)."""
from django.core.management.base import BaseCommand

from dropship.images import fetch_pending
from dropship.models import SupplierProduct


class Command(BaseCommand):
    help = 'Récupère les images des produits importés depuis un fichier fournisseur.'

    def add_arguments(self, parser):
        parser.add_argument('--minutes', type=int, default=10, help='Temps maximum (minutes)')

    def handle(self, *args, **opts):
        pending = SupplierProduct.objects.exclude(image_urls=[]).select_related('product')
        saved, left = fetch_pending(pending, budget_seconds=opts['minutes'] * 60)
        self.stdout.write(f'{saved} image(s) enregistrée(s), {left} produit(s) restant(s).')
