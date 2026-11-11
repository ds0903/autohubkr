from django.contrib import admin, messages

from . import services
from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("provider", "rating", "short_comment", "status", "client", "created_at")
    list_filter = ("status", "rating")
    search_fields = ("provider__name", "comment")
    readonly_fields = ("order", "provider", "client", "rating", "comment", "moderated_by", "moderated_at")
    actions = ["approve_selected", "reject_selected"]

    @admin.display(description="Текст відгуку")
    def short_comment(self, obj):
        return (obj.comment[:80] + "…") if len(obj.comment) > 80 else (obj.comment or "—")

    @admin.action(description="Опублікувати вибрані відгуки")
    def approve_selected(self, request, queryset):
        for review in queryset:
            services.moderate(review, approve=True, by=request.user)
        self.message_user(request, f"Опубліковано: {queryset.count()}", messages.SUCCESS)

    @admin.action(description="Відхилити вибрані відгуки")
    def reject_selected(self, request, queryset):
        for review in queryset:
            services.moderate(review, approve=False, by=request.user, reason="Не відповідає правилам модерації")
        self.message_user(request, f"Відхилено: {queryset.count()}", messages.WARNING)

    def has_add_permission(self, request):
        return False  # відгуки створюються лише за підтвердженими заявками
