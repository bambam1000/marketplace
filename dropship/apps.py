from django.apps import AppConfig


class DropshipConfig(AppConfig):
    name = 'dropship'
    verbose_name = 'Dropshipping'

    def ready(self):
        from django.db import transaction
        from django.db.models.signals import post_save

        from orders.models import Order

        def order_saved(sender, instance, **kwargs):
            if instance.is_paid and instance.status not in ('cancelled', 'refunded'):
                from .cj_sync import on_order_saved
                transaction.on_commit(lambda: on_order_saved(instance))

        post_save.connect(order_saved, sender=Order, dispatch_uid='dropship_auto_send')
