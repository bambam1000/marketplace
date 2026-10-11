"""Synchronisation des fournisseurs connectés (CJ) — à planifier toutes les heures, par exemple :

    python manage.py dropship_sync --minutes 10

1. transmet les commandes payées (fournisseurs en transmission automatique) ;
2. récupère statut et numéro de suivi des commandes transmises (le client est prévenu à l'expédition) ;
3. met à jour prix, stocks et variantes des produits, en commençant par les moins récemment vérifiés.
"""
import time

from django.core.management.base import BaseCommand

from dropship.cj import CJError
from dropship.cj_sync import auto_send, refresh_open_orders, sync_items
from dropship.models import SupplierConnection


class Command(BaseCommand):
    help = 'Synchronise commandes, suivis, prix et stocks avec les fournisseurs connectés (CJ).'

    def add_arguments(self, parser):
        parser.add_argument('--minutes', type=float, default=10, help='Durée maximale (défaut : 10 min)')
        parser.add_argument('--no-products', action='store_true', help='Seulement les commandes')

    def handle(self, *args, minutes, no_products, **opts):
        deadline = time.monotonic() + minutes * 60
        conns = (SupplierConnection.objects.filter(provider='cj', is_active=True, warehouse__is_active=True)
                 .exclude(api_key_enc='').select_related('warehouse__store'))
        for conn in conns:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                sent, errors = auto_send(conn) if conn.auto_send else (0, [])
                changed, errs = refresh_open_orders(conn, budget_seconds=max(5, left / 3))
                errors += errs
                done = remaining = 0
                if not no_products:
                    items = list(conn.items.select_related('product').order_by('last_synced_at')[:500])
                    done, remaining, errs = sync_items(conn, items, budget_seconds=max(5, deadline - time.monotonic()))
                    errors += errs
            except CJError as exc:
                conn.api_error = str(exc)[:300]
                conn.save(update_fields=['api_error'])
                self.stderr.write(f'{conn.name} : {exc}')
                continue
            if conn.api_error:
                conn.api_error = ''
                conn.save(update_fields=['api_error'])
            self.stdout.write(f'{conn.name} : {sent} commande(s) transmise(s), {changed} suivi(s) mis à jour, '
                              f'{done} produit(s) synchronisé(s), {remaining} restant(s).')
            for e in errors[:10]:
                self.stderr.write(f'  {e}')
