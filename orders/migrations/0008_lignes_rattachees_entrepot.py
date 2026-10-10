"""Les commandes se consultent désormais depuis chaque entrepôt : les anciennes lignes sans entrepôt
sont rattachées à l'entrepôt principal de leur boutique (sinon au premier entrepôt actif)."""
from django.db import migrations


def attach(apps, schema_editor):
    OrderItem = apps.get_model('orders', 'OrderItem')
    Warehouse = apps.get_model('inventory', 'Warehouse')
    cache = {}
    for item in OrderItem.objects.filter(warehouse__isnull=True).only('pk', 'store_id'):
        if item.store_id not in cache:
            whs = Warehouse.objects.filter(store_id=item.store_id)
            cache[item.store_id] = (whs.filter(is_default=True).first() or whs.filter(is_active=True).first() or whs.first())
        wh = cache[item.store_id]
        if wh is not None:
            OrderItem.objects.filter(pk=item.pk).update(warehouse=wh)


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0007_historique_statuts'),
        ('inventory', '0003_warehouse_linked_stores'),
    ]
    operations = [migrations.RunPython(attach, migrations.RunPython.noop)]
