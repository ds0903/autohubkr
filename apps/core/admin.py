from django.contrib import admin

from .admin_cards import CardListAdminMixin, short_dt
from .models import City, Notification

# Масове видалення із списку вимкнено: видалення йде з картки запису, з підтвердженням і переліком наслідків.
admin.site.disable_action("delete_selected")


@admin.register(City)
class CityAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    add_label = "Додати місто"
    card_tab_filter = "is_active"
    card_toggles = (("is_active", ("Активне", "Вимкнене")),)

    def card_subtitle(self, obj):
        return obj.slug


@admin.register(Notification)
class NotificationAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "kind", "created_at", "sent_at", "attempts")
    list_filter = ("kind",)
    list_select_related = ("user",)
    readonly_fields = ("user", "kind", "text", "payload", "dedupe_key", "sent_at", "attempts", "error")
    card_bar_filters = ("kind",)

    def has_add_permission(self, request):
        return False

    def card_title(self, obj):
        return f"Сповіщення №{obj.pk}"

    def card_subtitle(self, obj):
        return f"{obj.kind} → {obj.user.get_full_name() or obj.user.get_username()}"

    def card_text(self, obj):
        return obj.text

    def card_badges(self, obj):
        if obj.sent_at:
            return [("Надіслано", "ok")]
        return [("Помилка", "danger")] if obj.error else [("У черзі", "warn")]

    def card_meta(self, obj):
        return [
            ("Створено", short_dt(obj.created_at)),
            ("Надіслано", short_dt(obj.sent_at) if obj.sent_at else None),
            ("Спроб", obj.attempts or None),
        ]
