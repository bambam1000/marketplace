from django.db import models
from django.conf import settings


class CartItem(models.Model):
    """Panier persistant pour les utilisateurs connectés"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    color = models.CharField(max_length=50, blank=True)
    size = models.CharField(max_length=50, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product', 'color', 'size')

    def __str__(self):
        return f"{self.user.username} - {self.product.name} x{self.quantity}"
