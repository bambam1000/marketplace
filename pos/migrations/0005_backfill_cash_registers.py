"""Une caisse « Caisse principale » par entrepôt actif, et rattachement des sessions existantes.

- Les sessions sans entrepôt sont rattachées à l'entrepôt par défaut de leur boutique.
- Si plusieurs sessions sont ouvertes sur la même caisse, seule la plus récente reste ouverte.
- Les ventes déjà remboursées (ancien système : remboursement total) reçoivent leurs montants remboursés.
"""
from django.db import migrations
from django.db.models import F
from django.utils import timezone


def backfill(apps, schema_editor):
    Warehouse = apps.get_model('inventory', 'Warehouse')
    CashRegister = apps.get_model('pos', 'CashRegister')
    POSSession = apps.get_model('pos', 'POSSession')
    POSSale = apps.get_model('pos', 'POSSale')
    POSSaleItem = apps.get_model('pos', 'POSSaleItem')

    used = set(POSSession.objects.exclude(warehouse__isnull=True).values_list('warehouse_id', flat=True))
    defaults = dict(Warehouse.objects.filter(is_default=True).values_list('store_id', 'pk'))
    for store_id, warehouse_id in defaults.items():
        POSSession.objects.filter(store_id=store_id, warehouse__isnull=True).update(warehouse_id=warehouse_id)
        used.add(warehouse_id)

    registers = {}
    for wh in Warehouse.objects.filter(pk__in=used) | Warehouse.objects.filter(is_active=True):
        if wh.pk in registers:
            continue
        reg = CashRegister.objects.filter(warehouse=wh).first() or CashRegister.objects.create(
            store_id=wh.store_id, warehouse=wh, name='Caisse principale', code=f'{wh.code}-C1'[:20])
        registers[wh.pk] = reg

    for session in POSSession.objects.filter(register__isnull=True).exclude(warehouse__isnull=True).order_by('-opened_at'):
        reg = registers.get(session.warehouse_id)
        if reg is None:
            continue
        if session.status == 'open' and POSSession.objects.filter(register=reg, status='open').exists():
            session.status = 'closed'
            session.closed_at = timezone.now()
            session.notes = (session.notes + '\n' if session.notes else '') + 'Fermée automatiquement : une autre session était ouverte sur la même caisse.'
        session.register = reg
        session.save()

    POSSale.objects.filter(status='refunded').update(refunded_amount=F('total_amount'), refunded_tax=F('tax_amount'))
    POSSaleItem.objects.filter(sale__status='refunded').update(refunded_quantity=F('quantity'))


class Migration(migrations.Migration):

    dependencies = [
        ('pos', '0004_cash_registers'),
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
