"""Rattache les sessions et ventes POS existantes à l'entrepôt par défaut de leur boutique.

Avant cette version, la caisse ne précisait pas d'entrepôt : Product.adjust_stock sortait
alors le stock de l'entrepôt par défaut de la boutique. C'est donc bien de là que le stock
de ces ventes est parti.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Warehouse = apps.get_model('inventory', 'Warehouse')
    POSSession = apps.get_model('pos', 'POSSession')
    POSSale = apps.get_model('pos', 'POSSale')
    defaults = dict(Warehouse.objects.filter(is_default=True).values_list('store_id', 'pk'))
    for store_id, warehouse_id in defaults.items():
        POSSession.objects.filter(store_id=store_id, warehouse__isnull=True).update(warehouse_id=warehouse_id)
        POSSale.objects.filter(store_id=store_id, warehouse__isnull=True).update(warehouse_id=warehouse_id)


class Migration(migrations.Migration):

    dependencies = [
        ('pos', '0002_possale_warehouse_possaleitem_unit_cost_and_more'),
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
