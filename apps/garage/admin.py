from django.contrib import admin

from .models import GarageRecord, MaintenanceRule, Receipt, Vehicle


class GarageRecordInline(admin.TabularInline):
    model = GarageRecord
    extra = 0
    fields = ("date", "kind", "title", "mileage", "amount", "trust")


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("display_name", "owner", "vin", "mileage", "data_source")
    search_fields = ("vin", "model_name", "nickname", "owner__username", "owner__phone")
    list_filter = ("brand", "data_source")
    raw_id_fields = ("owner",)
    inlines = [GarageRecordInline]


@admin.register(MaintenanceRule)
class MaintenanceRuleAdmin(admin.ModelAdmin):
    list_display = ("title", "brand", "interval_km", "interval_months", "is_active")
    list_filter = ("brand", "is_active")
    search_fields = ("title",)


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "vehicle", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("vehicle",)


@admin.register(GarageRecord)
class GarageRecordAdmin(admin.ModelAdmin):
    list_display = ("title", "vehicle", "date", "amount", "trust")
    list_filter = ("kind", "trust")
    search_fields = ("title", "vehicle__vin")
    raw_id_fields = ("vehicle", "provider", "order", "receipt")
