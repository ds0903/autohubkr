from django.contrib import admin

from .models import City, Notification


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "kind", "created_at", "sent_at", "attempts")
    list_filter = ("kind",)
    readonly_fields = ("user", "kind", "text", "payload", "dedupe_key", "sent_at", "attempts", "error")

    def has_add_permission(self, request):
        return False
