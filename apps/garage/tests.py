import tempfile
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from apps.catalog.models import CarBrand
from apps.core.testing import make_world
from apps.garage import services
from apps.garage.models import GarageRecord, MaintenanceRule, Receipt, Vehicle


class VehicleFromTextTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def test_brand_model_and_year_are_split(self):
        v = services.vehicle_from_text("toyota   Camry 2015")
        self.assertEqual((v.brand, v.model_name, v.year), (self.w["toyota"], "Camry", 2015))
        self.assertIsNone(v.pk, "розбір нічого не зберігає")

    def test_year_can_come_first_and_model_can_be_empty(self):
        v = services.vehicle_from_text("2012 Toyota")
        self.assertEqual((v.brand, v.model_name, v.year), (self.w["toyota"], "", 2012))

    def test_two_word_brand_wins_over_one_word(self):
        rover = CarBrand.objects.create(name="Land Rover", slug="land-rover")
        v = services.vehicle_from_text("Land Rover Discovery 2018")
        self.assertEqual((v.brand, v.model_name), (rover, "Discovery"))

    def test_unknown_brand_stays_in_model_and_catalog_is_untouched(self):
        before = CarBrand.objects.count()
        v = services.vehicle_from_text("Geely Coolray 2021")
        self.assertIsNone(v.brand)
        self.assertEqual((v.model_name, v.year), ("Geely Coolray", 2021))
        self.assertEqual(CarBrand.objects.count(), before)

    def test_long_input_is_cut_to_model_field_size(self):
        self.assertEqual(len(services.vehicle_from_text("слово " * 40).model_name), 80)


class VinTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def test_decode_brand_and_year(self):
        info = services.decode_vin("WVWZZZ1KZAW123456")
        self.assertEqual(info.brand_name, "Volkswagen")
        self.assertEqual(info.year, 2010)

    def test_fill_vehicle_from_vin(self):
        v = Vehicle.objects.create(owner=self.w["client"], vin="wvwzzz1kzaw123456")
        services.fill_vehicle_from_vin(v)
        v.refresh_from_db()
        self.assertEqual(v.vin, "WVWZZZ1KZAW123456")
        self.assertEqual(str(v.brand), "Volkswagen")
        self.assertEqual(v.year, 2010)
        self.assertEqual(v.data_source, Vehicle.DataSource.VIN_DECODED)

    def test_vin_validation_rejects_forbidden_letters(self):
        v = Vehicle(owner=self.w["client"], vin="WVWZZZ1KZAW12345O")
        with self.assertRaises(ValidationError):
            v.full_clean()

    def test_unknown_wmi_leaves_manual(self):
        v = Vehicle.objects.create(owner=self.w["client"], vin="11111111111111111")
        services.fill_vehicle_from_vin(v)
        self.assertIsNone(v.brand)


class PlanAndExpensesTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.vehicle = self.w["vehicle"]
        self.rule = MaintenanceRule.objects.create(title="Мастило", interval_km=10000, interval_months=12)

    def test_plan_overdue_by_mileage(self):
        GarageRecord.objects.create(vehicle=self.vehicle, title="Мастило", date=date(2026, 1, 10), mileage=138000,
                                    rule=self.rule)
        plan, disclaimer = services.maintenance_plan(self.vehicle, today=date(2026, 2, 1))
        item = next(i for i in plan if i.rule == self.rule)
        self.assertEqual(item.due_km, 148000)
        self.assertEqual(item.status, "overdue")  # пробіг 150000 > 148000
        self.assertIn("сервісною книжкою", disclaimer)

    def test_plan_ok_and_soon_by_date(self):
        GarageRecord.objects.create(vehicle=self.vehicle, title="Мастило", date=date(2026, 1, 10), mileage=149500,
                                    rule=self.rule)
        plan, _ = services.maintenance_plan(self.vehicle, today=date(2026, 2, 1))
        item = next(i for i in plan if i.rule == self.rule)
        self.assertEqual(item.status, "ok")
        plan, _ = services.maintenance_plan(self.vehicle, today=date(2026, 12, 20))
        self.assertEqual(next(i for i in plan if i.rule == self.rule).status, "soon")

    def test_plan_without_records_is_unknown(self):
        plan, _ = services.maintenance_plan(self.vehicle, today=date(2026, 2, 1))
        self.assertEqual(next(i for i in plan if i.rule == self.rule).status, "unknown")

    def test_month_end_math(self):
        self.assertEqual(services._add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(services._add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_monthly_expenses(self):
        GarageRecord.objects.create(vehicle=self.vehicle, title="Пальне", kind="fuel", date=date(2026, 3, 5), amount=Decimal("1000"))
        GarageRecord.objects.create(vehicle=self.vehicle, title="Колодки", kind="parts", date=date(2026, 3, 20), amount=Decimal("500"))
        GarageRecord.objects.create(vehicle=self.vehicle, title="Пальне", kind="fuel", date=date(2026, 4, 2), amount=Decimal("700"))
        rows = services.monthly_expenses(self.vehicle)
        self.assertEqual([r["total"] for r in rows], [Decimal("700"), Decimal("1500")])
        self.assertEqual(rows[1]["by_kind"]["Пальне"], Decimal("1000"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReceiptTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.receipt = Receipt(vehicle=self.w["vehicle"])
        self.receipt.image.save("r.jpg", ContentFile(b"fake-image-bytes"), save=True)

    def test_successful_parse_creates_owner_claimed_record(self):
        def fake(data, mime):
            self.assertEqual(data, b"fake-image-bytes")
            return {"date": "2026-05-04", "mileage": 151000, "total": 2350.5, "title": "Заміна колодок",
                    "works": ["Заміна колодок"], "parts": ["Колодки передні"], "kind": "service"}

        record = services.process_receipt(self.receipt, parse=fake)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, Receipt.Status.DONE)
        self.assertEqual(record.amount, Decimal("2350.50"))
        self.assertEqual(record.date, date(2026, 5, 4))
        self.assertEqual(record.trust, GarageRecord.Trust.OWNER_CLAIMED)
        self.assertEqual(record.receipt, self.receipt)

    def test_garbage_fields_do_not_crash(self):
        record = services.process_receipt(
            self.receipt, parse=lambda d, m: {"date": "не дата", "total": "abc", "kind": "???", "mileage": "x"})
        self.assertIsNotNone(record)
        self.assertIsNone(record.amount)
        self.assertEqual(record.kind, GarageRecord.Kind.SERVICE)

    def test_parse_failure_marks_receipt_failed(self):
        def boom(data, mime):
            raise RuntimeError("API недоступний")

        self.assertIsNone(services.process_receipt(self.receipt, parse=boom))
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, Receipt.Status.FAILED)
        self.assertIn("API недоступний", self.receipt.error)
        self.assertEqual(GarageRecord.objects.count(), 0)
