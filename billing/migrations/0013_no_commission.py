"""Comptoir ne prend plus de commission sur les ventes : il se rémunère uniquement par l'abonnement."""
from django.db import migrations


def no_commission(apps, schema_editor):
    apps.get_model('billing', 'SubscriptionPlan').objects.update(commission_rate=0)


class Migration(migrations.Migration):
    dependencies = [('billing', '0012_grant_and_settings')]
    operations = [migrations.RunPython(no_commission, migrations.RunPython.noop)]
