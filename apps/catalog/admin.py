from django.conf import settings
from django.contrib import admin

from apps.core.admin_cards import CardListAdminMixin, checkbox_list

from .models import CarBrand, Category, Provider, ProviderPhoto, Service, ServiceType

DATA_STATUS_TONE = {
    Provider.DataStatus.COLLECTED: "warn",
    Provider.DataStatus.OWNER_CONFIRMED: "road",
    Provider.DataStatus.VERIFIED: "ok",
}


@admin.register(Category)
class CategoryAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("name", "revenue_model", "sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    add_label = "Додати категорію"
    card_toggles = (("is_active", ("Активна", "Прихована")),)

    def card_subtitle(self, obj):
        return obj.get_revenue_model_display()

    def card_text(self, obj):
        return obj.description

    def card_meta(self, obj):
        return [("Порядок", obj.sort_order)]


@admin.register(ServiceType)
class ServiceTypeAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("name", "category", "is_safety_critical", "is_active")
    list_filter = ("category", "is_safety_critical", "is_active")
    list_select_related = ("category",)
    search_fields = ("name", "keywords")
    prepopulated_fields = {"slug": ("name",)}
    add_label = "Додати тип послуги"
    card_tab_filter = "is_active"
    card_bar_filters = ("category",)
    card_toggles = (("is_active", ("Активна", "Прихована")),)

    def card_subtitle(self, obj):
        return obj.category.name

    def card_text(self, obj):
        return obj.keywords

    def card_badges(self, obj):
        return [("Безпека", "danger")] if obj.is_safety_critical else []


@admin.register(CarBrand)
class CarBrandAdmin(CardListAdminMixin, admin.ModelAdmin):
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    add_label = "Додати марку"


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 1


class ProviderPhotoInline(admin.TabularInline):
    model = ProviderPhoto
    extra = 0


@admin.action(description="Показати посилання: підтвердження даних і особиста ссилка/QR")
def show_provider_links(modeladmin, request, queryset):
    from django.contrib import messages
    from django.urls import reverse

    from apps.web.views import make_confirm_token

    for p in queryset:
        confirm = f"{settings.SITE_URL}{reverse('web:provider_confirm', args=[make_confirm_token(p)])}"
        ref = f"{settings.SITE_URL}{reverse('web:referral', args=[p.referral_code])}"
        qr = f"{settings.SITE_URL}{reverse('web:referral_qr', args=[p.referral_code])}"
        modeladmin.message_user(
            request, f"«{p.name}»: підтвердження даних: {confirm}  ·  особиста ссилка: {ref}  ·  QR: {qr}", messages.INFO)


@admin.register(Provider)
class ProviderAdmin(CardListAdminMixin, admin.ModelAdmin):
    actions = [show_provider_links]
    list_display = (
        "name",
        "city",
        "district",
        "data_status",
        "rating",
        "confirmed_deals_count",
        "avg_response_minutes",
        "is_active",
    )
    list_filter = ("city", "data_status", "categories", "is_active")
    list_select_related = ("city",)
    search_fields = ("name", "address", "district", "phone")
    raw_id_fields = ("owner",)
    readonly_fields = ("rating", "reviews_count", "confirmed_deals_count", "avg_response_minutes")
    inlines = [ServiceInline, ProviderPhotoInline]
    add_label = "Додати виконавця"
    card_tab_filter = "data_status"
    card_bar_filters = ("city",)
    card_toggles = (("is_active", ("У каталозі", "Прихований")),)
    # Заголовки лишаються короткими; пояснення йдуть описом.
    fieldsets = (
        ("Основне", {"fields": ("name", "city", "categories", "description", "district", "address", "working_hours")}),
        ("Контакти", {
            "description": "Приховані від клієнта до підтвердження запису.",
            "fields": ("phone", "telegram_username", "website", "owner"),
        }),
        ("Спеціалізація", {"fields": ("brands",)}),
        ("Просування", {"fields": ("data_status", "source_note", "promoted_until", "is_active")}),
        ("Статистика", {
            "description": "Рахується автоматично.",
            "fields": ("rating", "reviews_count", "confirmed_deals_count", "avg_response_minutes"),
        }),
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        # Категорії й марки ставляться галочками: ні двох списків із перенесенням, ні пошукового віджета.
        if db_field.name in ("categories", "brands"):
            kwargs["widget"] = checkbox_list()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def card_subtitle(self, obj):
        return " · ".join(part for part in (obj.city.name, obj.district) if part)

    def card_badges(self, obj):
        badges = [(obj.get_data_status_display(), DATA_STATUS_TONE.get(obj.data_status, "muted"))]
        if obj.is_promoted:
            badges.append(("Реклама", "signal"))
        return badges

    def card_meta(self, obj):
        return [
            ("Рейтинг", f"{obj.rating} ({obj.reviews_count})" if obj.reviews_count else None),
            ("Угод", obj.confirmed_deals_count),
            ("Відповідь", f"{obj.avg_response_minutes} хв" if obj.avg_response_minutes is not None else None),
            ("Телефон", obj.phone),
        ]


@admin.register(ProviderPhoto)
class ProviderPhotoAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("provider", "kind", "is_approved", "created_at")
    list_filter = ("kind", "is_approved")
    list_select_related = ("provider",)
    raw_id_fields = ("provider",)
    add_label = "Додати фото"
    card_tab_filter = "is_approved"
    card_toggles = (("is_approved", ("Схвалено", "На модерації")),)

    def card_title(self, obj):
        return obj.provider.name

    def card_subtitle(self, obj):
        return obj.caption or obj.get_kind_display()

    def card_image(self, obj):
        return obj.image.url if obj.image else None

    def card_badges(self, obj):
        return [(obj.get_kind_display(), "muted")]
