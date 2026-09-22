from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Envoie les campagnes email programmées dont l'heure est arrivée"

    def handle(self, *args, **options):
        from marketing.models import Newsletter
        from marketing.views import _send_newsletter_now

        due = Newsletter.objects.filter(status='scheduled', scheduled_at__lte=timezone.now())
        count = 0
        for newsletter in due:
            sent = _send_newsletter_now(newsletter)
            self.stdout.write(self.style.SUCCESS(
                f'"{newsletter.subject}" envoyée à {sent} destinataire(s).'
            ))
            count += 1
        if count == 0:
            self.stdout.write('Aucune campagne à envoyer.')
