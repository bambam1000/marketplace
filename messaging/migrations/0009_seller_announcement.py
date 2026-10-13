"""Première annonce : inviter visiteurs et acheteurs à ouvrir leur boutique (modifiable dans Admin > Annonces)."""
from django.db import migrations


def create(apps, schema_editor):
    Announcement = apps.get_model('messaging', 'Announcement')
    if not Announcement.objects.exists():
        Announcement.objects.create(
            title='Vous vendez ? Ouvrez votre boutique sur Comptoir',
            message='30 jours gratuits, sans moyen de paiement et 0 % de commission sur vos ventes.',
            link_url='/devenir-vendeur/', link_label='Devenir vendeur',
            audience='buyers', tone='promo', dismissible=True, priority=0,
        )


class Migration(migrations.Migration):
    dependencies = [('messaging', '0008_announcements')]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
