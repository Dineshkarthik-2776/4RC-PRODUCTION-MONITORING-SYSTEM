from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    model = User
    list_display = ('employee_id', 'name', 'role', 'status', 'is_staff', 'is_active')
    list_filter = ('role', 'status', 'is_staff', 'is_active')
    fieldsets = (
        (None, {'fields': ('employee_id', 'password', 'name', 'role')}),
        ('Account', {'fields': ('status', 'is_active')}),
        ('Permissions', {
            'fields': ('is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'description': 'An administrator is a Manager with is_staff and '
                           'is_superuser set. There is no separate admin role.',
        }),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('employee_id', 'name', 'role', 'password1', 'password2',
                       'status', 'is_staff', 'is_active'),
        }),
    )
    search_fields = ('employee_id', 'name')
    ordering = ('employee_id',)
