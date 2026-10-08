from django.core.management.base import BaseCommand

from whatsapp import services


class Command(BaseCommand):
    help = "Relance tout de suite les messages WhatsApp en échec (le worker le fait aussi automatiquement)."

    def handle(self, *args, **options):
        if not services.is_enabled():
            self.stdout.write('WhatsApp désactivé (WHATSAPP_ENABLED / EVOLUTION_API_KEY).')
            return
        sent, total = services.retry_failed()
        self.stdout.write(self.style.SUCCESS(f'{sent}/{total} message(s) envoyé(s).'))
