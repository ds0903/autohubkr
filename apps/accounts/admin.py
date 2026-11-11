from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "phone", "role", "city", "is_active")
    list_filter = ("role", "city", "is_active", "is_staff")
    search_fields = ("username", "first_name", "last_name", "phone", "email")
    fieldsets = UserAdmin.fieldsets + (
        (
            "Платформа",
            {
                "fields": (
                    "role",
                    "phone",
                    "telegram_id",
                    "city",
                    "personal_data_consent_at",
                    "ai_dialog_consent",
                )
            },
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Платформа", {"fields": ("role", "phone", "city")}),
    )
