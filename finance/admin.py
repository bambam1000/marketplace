from django.contrib import admin
from .models import Commission, Payout, Expense, ProductBoost, ActivityLog
admin.site.register(Commission)
admin.site.register(Payout)
admin.site.register(Expense)
admin.site.register(ProductBoost)
admin.site.register(ActivityLog)
