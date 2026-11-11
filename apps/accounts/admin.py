from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.core.admin_cards import CardListAdminMixin, checkbox_list

from .models import User

ROLE_TONE = {User.Role.CLIENT: "muted", User.Role.PROVIDER: "signal", User.Role.LIAISON: "road", User.Role.ADMIN: "ink"}


@admin.register(User)
class CustomUserAdmin(CardListAdminMixin, UserAdmin):
    list_display = ("username", "first_name", "phone", "role", "city", "is_active")
    list_filter = ("role", "city", "is_active", "is_staff")
    list_select_related = ("city",)
    search_fields = ("username", "first_name", "last_name", "phone", "email")
    # Права обираються галочками, а не двома списками з перенесенням.
    filter_horizontal = ()
    add_label = "Додати користувача"
    card_tab_filter = "role"
    card_bar_filters = ("city",)
    card_toggles = (("is_active", ("Активний", "Вимкнений")),)
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

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in ("groups", "user_permissions"):
            kwargs["widget"] = checkbox_list()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def _toggles(self, request, obj):
        # Власний акаунт не вимикаємо одним кліком: можна загубити доступ до панелі.
        return [] if obj.pk == request.user.pk else super()._toggles(request, obj)

    def card_title(self, obj):
        return obj.get_full_name() or obj.username

    def card_subtitle(self, obj):
        return obj.username if obj.get_full_name() else obj.email

    def card_badges(self, obj):
        badges = [(obj.get_role_display(), ROLE_TONE.get(obj.role, "muted"))]
        if not obj.is_active:
            badges.append(("Вимкнений", "danger"))
        return badges

    def card_meta(self, obj):
        return [
            ("Телефон", obj.phone),
            ("Місто", obj.city.name if obj.city_id else None),
            ("Telegram", "підключено" if obj.telegram_id else None),
        ]
