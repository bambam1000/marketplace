"""Revérifie les paiements en ligne restés « en attente » — à planifier toutes les 10 minutes :

    python manage.py billing_check_payments

Utile quand une notification du prestataire n'arrive pas (site injoignable, client qui ferme la page).
Les paiements en attente depuis plus de 48 h passent « expirés ».
"""
from django.core.management.base import BaseCommand

from billing.online import check_pending


class Command(BaseCommand):
    help = 'Revérifie auprès des prestataires les paiements en ligne en attente.'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=48, help='Âge maximum des paiements revérifiés (défaut : 48 h)')

    def handle(self, *args, hours, **opts):
        done, errors, expired = check_pending(max_age_hours=hours)
        self.stdout.write(f'{done} paiement(s) vérifié(s), {errors} erreur(s), {expired} expiré(s).')
