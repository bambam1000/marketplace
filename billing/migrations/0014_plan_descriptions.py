"""Descriptions des formules sans référence à la commission (supprimée)."""
from django.db import migrations


def update(apps, schema_editor):
    Plan = apps.get_model('billing', 'SubscriptionPlan')
    Plan.objects.filter(slug='enterprise', description__icontains='commission').update(
        description='Sans limite, avec un interlocuteur dédié et la formation de votre équipe.')


class Migration(migrations.Migration):
    dependencies = [('billing', '0013_no_commission')]
    operations = [migrations.RunPython(update, migrations.RunPython.noop)]
