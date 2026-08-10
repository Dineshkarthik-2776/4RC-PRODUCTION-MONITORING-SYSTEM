from django.contrib import admin
from .models import RawLineData, RecipeMaster, ChangeoverSummary

# This "registers" your models, making them visible in the admin panel.
admin.site.register(RawLineData)
admin.site.register(RecipeMaster)
admin.site.register(ChangeoverSummary)