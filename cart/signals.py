from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .utils import merge_session_cart


@receiver(user_logged_in)
def merge_cart_on_login(sender, request, user, **kwargs):
    """Fusionne le panier de session dans le panier persistant à la connexion."""
    merge_session_cart(request, user)
