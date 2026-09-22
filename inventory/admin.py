from django.contrib import admin
from .models import Warehouse, ProductStock, StockTransfer, TransferItem

admin.site.register(Warehouse)
admin.site.register(ProductStock)
admin.site.register(StockTransfer)
admin.site.register(TransferItem)
