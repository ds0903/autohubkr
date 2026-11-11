from django.contrib import admin, messages

from apps.core.admin_cards import CardListAdminMixin, short_dt

from . import services
from .models import Review

REVIEW_TONE = {Review.Status.PENDING: "warn", Review.Status.APPROVED: "ok", Review.Status.REJECTED: "danger"}


@admin.register(Review)
class ReviewAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("provider", "rating", "short_comment", "status", "client", "created_at")
    list_filter = ("status", "rating")
    list_select_related = ("provider", "client")
    search_fields = ("provider__name", "comment")
    readonly_fields = ("order", "provider", "client", "rating", "comment", "moderated_by", "moderated_at")
    actions = ["approve_selected", "reject_selected"]
    card_tab_filter = "status"
    card_empty_text = "Відгуки з'являться після підтверджених робіт: клієнт залишає їх, а ви перевіряєте тут."

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

    def card_title(self, obj):
        return obj.provider.name

    def card_subtitle(self, obj):
        return f"Заявка №{obj.order_id} · {obj.client.get_full_name() or obj.client.get_username()}"

    def card_text(self, obj):
        return obj.comment

    def card_badges(self, obj):
        return [(f"★ {obj.rating}", "signal"), (obj.get_status_display(), REVIEW_TONE.get(obj.status, "muted"))]

    def card_meta(self, obj):
        return [("Створено", short_dt(obj.created_at)), ("Причина відхилення", obj.reject_reason)]
