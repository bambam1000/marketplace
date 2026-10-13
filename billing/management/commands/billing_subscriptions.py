"""Rappels de fin d'abonnement (J-7, J-2) et expiration des essais et formules échus.

À lancer une fois par jour (cron, timer systemd ou Planificateur de tâches Windows) :
    python manage.py billing_subscriptions
    python manage.py billing_subscriptions --dry-run     # voir ce qui serait fait, sans rien modifier
"""
from django.core.management.base import BaseCommand

from billing.subscriptions import run_daily


class Command(BaseCommand):
    help = "Envoie les rappels de fin d'abonnement et masque les boutiques dont l'abonnement a expiré."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Affiche ce qui serait fait sans rien modifier ni envoyer.')

    def handle(self, *args, dry_run=False, **options):
        report = []
        reminders, expired = run_daily(dry_run=dry_run, report=report)
        for line in report:
            self.stdout.write(line)
        prefix = 'Simulation : ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{prefix}{reminders} rappel(s), {expired} abonnement(s) expiré(s).'))
