"""Répare les liens des notifications déjà envoyées.

- « Nouvelle demande de devis » pointait vers /commandes/devis/<id>/ (inexistant) :
  la demande se consulte dans l'espace vendeur, /dashboard/devis/<id>/.
- « Offre reçue » pointait vers /commandes/mes-devis/ (inexistant) : /commandes/rfq/mes-demandes/.
- Les messages WhatsApp reçus étaient rangés en « Système » : type « Messages ».
"""
import re

from django.db import migrations

RFQ = re.compile(r'^/commandes/devis/(\d+)/$')


def fix(apps, schema_editor):
    Notification = apps.get_model('messaging', 'Notification')
    for n in Notification.objects.filter(url__startswith='/commandes/devis/'):
        m = RFQ.match(n.url)
        if m:
            n.url = f'/dashboard/devis/{m.group(1)}/'
            n.save(update_fields=['url'])
    Notification.objects.filter(url='/commandes/mes-devis/').update(url='/commandes/rfq/mes-demandes/')
    Notification.objects.filter(notif_type='system', title__startswith='Nouveau message WhatsApp').update(notif_type='message')


class Migration(migrations.Migration):

    dependencies = [
        ('messaging', '0006_notification_types_index'),
    ]

    operations = [
        migrations.RunPython(fix, migrations.RunPython.noop),
    ]
