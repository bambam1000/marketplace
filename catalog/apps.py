from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = 'catalog'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        def merge_browsing(sender, request, user, **kwargs):
            # L'historique de navigation du visiteur rejoint son compte à la connexion
            if request is not None:
                from .recommend import merge_session
                try:
                    merge_session(request, user)
                except Exception:  # noqa: BLE001
                    pass
        user_logged_in.connect(merge_browsing, dispatch_uid='catalog_merge_browsing')
