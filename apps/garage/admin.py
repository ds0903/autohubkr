from django.contrib import admin

from apps.core.admin_cards import CardListAdminMixin, money, short_dt

from .models import GarageRecord, MaintenanceRule, Receipt, Vehicle

RECEIPT_TONE = {Receipt.Status.PENDING: "warn", Receipt.Status.DONE: "ok", Receipt.Status.FAILED: "danger"}
TRUST_TONE = {GarageRecord.Trust.OWNER_CLAIMED: "muted", GarageRecord.Trust.PROVIDER_CONFIRMED: "ok"}


def owner_name(user) -> str:
    return (user.get_full_name() or user.get_username()) if user else ""


class GarageRecordInline(admin.TabularInline):
    model = GarageRecord
    extra = 0
    fields = ("date", "kind", "title", "mileage", "amount", "trust")


@admin.register(Vehicle)
class VehicleAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("display_name", "owner", "vin", "mileage", "data_source")
    search_fields = ("vin", "model_name", "nickname", "owner__username", "owner__phone")
    list_filter = ("brand", "data_source")
    list_select_related = ("owner", "brand")
    raw_id_fields = ("owner",)
    inlines = [GarageRecordInline]
    add_label = "Додати авто"
    card_tab_filter = "data_source"
    card_bar_filters = ("brand",)
    card_toggles = (("is_active", ("Активне", "Вимкнене")),)

    def card_title(self, obj):
        return obj.display_name

    def card_subtitle(self, obj):
        return f"Власник: {owner_name(obj.owner)}"

    def card_badges(self, obj):
        return [(obj.get_data_source_display(), "muted")]

    def card_meta(self, obj):
        return [
            ("VIN", obj.vin),
            ("Пробіг", f"{obj.mileage:,} км".replace(",", " ") if obj.mileage is not None else None),
            ("Двигун", obj.engine),
        ]


@admin.register(MaintenanceRule)
class MaintenanceRuleAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("title", "brand", "interval_km", "interval_months", "is_active")
    list_filter = ("brand", "is_active")
    list_select_related = ("brand",)
    search_fields = ("title",)
    add_label = "Додати норматив"
    card_tab_filter = "is_active"
    card_bar_filters = ("brand",)
    card_toggles = (("is_active", ("Діє", "Вимкнений")),)

    def card_subtitle(self, obj):
        return obj.brand.name if obj.brand_id else "Усі марки"

    def card_text(self, obj):
        return obj.note

    def card_meta(self, obj):
        return [
            ("Кожні", f"{obj.interval_km:,} км".replace(",", " ") if obj.interval_km else None),
            ("Або кожні", f"{obj.interval_months} міс." if obj.interval_months else None),
        ]


@admin.register(Receipt)
class ReceiptAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("id", "vehicle", "status", "created_at")
    list_filter = ("status",)
    list_select_related = ("vehicle__owner", "vehicle__brand")
    raw_id_fields = ("vehicle",)
    add_label = "Додати чек"
    card_tab_filter = "status"

    def card_title(self, obj):
        return f"Чек №{obj.pk}"

    def card_subtitle(self, obj):
        return obj.vehicle.display_name

    def card_text(self, obj):
        return obj.error

    def card_badges(self, obj):
        return [(obj.get_status_display(), RECEIPT_TONE.get(obj.status, "muted"))]

    def card_meta(self, obj):
        return [("Створено", short_dt(obj.created_at))]


@admin.register(GarageRecord)
class GarageRecordAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("title", "vehicle", "date", "amount", "trust")
    list_filter = ("kind", "trust")
    list_select_related = ("vehicle__brand",)
    search_fields = ("title", "vehicle__vin")
    raw_id_fields = ("vehicle", "provider", "order", "receipt")
    add_label = "Додати запис"
    card_tab_filter = "trust"

    def card_title(self, obj):
        return obj.title

    def card_subtitle(self, obj):
        return obj.vehicle.display_name

    def card_badges(self, obj):
        return [(obj.get_trust_display(), TRUST_TONE.get(obj.trust, "muted"))]

    def card_meta(self, obj):
        return [
            ("Тип", obj.get_kind_display()),
            ("Дата", short_dt(obj.date)),
            ("Сума", money(obj.amount) if obj.amount is not None else None),
            ("Пробіг", f"{obj.mileage:,} км".replace(",", " ") if obj.mileage is not None else None),
        ]
