from django.contrib import admin
from .models import *

for model in [m for m in dir() if not m.startswith('_') and m[0].isupper() and m not in ('admin',)]:
    try:
        admin.site.register(eval(model))
    except:
        pass
