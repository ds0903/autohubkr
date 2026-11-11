from django import forms
from django.conf import settings
from django.contrib import admin

from .models import CarBrand, Category, Provider, ProviderPhoto, Service, ServiceType


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "revenue_model", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_safety_critical", "is_active")
    list_filter = ("category", "is_safety_critical", "is_active")
    search_fields = ("name", "keywords")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(CarBrand)
class CarBrandAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 1
    autocomplete_fields = ("service_type",)


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
class ProviderAdmin(admin.ModelAdmin):
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
    search_fields = ("name", "address", "district", "phone")
    autocomplete_fields = ("brands",)
    raw_id_fields = ("owner",)
    readonly_fields = ("rating", "reviews_count", "confirmed_deals_count", "avg_response_minutes")
    inlines = [ServiceInline, ProviderPhotoInline]
    # Заголовки лишаються короткими, щоб вкладки не переносилися на другий рядок; пояснення йдуть описом.
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
        # Сім категорій зручніше ставити галочками, ніж двома списками з перенесенням.
        if db_field.name == "categories":
            kwargs["widget"] = forms.CheckboxSelectMultiple
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(ProviderPhoto)
class ProviderPhotoAdmin(admin.ModelAdmin):
    list_display = ("provider", "kind", "is_approved", "created_at")
    list_filter = ("kind", "is_approved")
    list_editable = ("is_approved",)
    raw_id_fields = ("provider",)
