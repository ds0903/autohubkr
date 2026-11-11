import re
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.catalog.models import Provider
from apps.core.models import City
from apps.core.testing import make_world


class ReferralCodeTests(TestCase):
    def test_codes_are_unique_short_and_url_safe(self):
        city = City.objects.create(name="Місто", slug="m")
        codes = {Provider.objects.create(city=city, name=f"П{i}").referral_code for i in range(25)}
        self.assertEqual(len(codes), 25)
        self.assertTrue(all(re.fullmatch(r"[a-z0-9]{8}", c) for c in codes), codes)

    def test_code_is_stable_across_saves(self):
        w = make_world()
        code = w["provider"].referral_code
        w["provider"].name = "Нова назва"
        w["provider"].save()
        w["provider"].refresh_from_db()
        self.assertEqual(w["provider"].referral_code, code)


class ImportProvidersTests(TestCase):
    HEADER = "name,categories,district,address,phone,telegram,website,working_hours,brands,source\n"

    def setUp(self):
        self.w = make_world()
        self.dir = tempfile.mkdtemp()

    def run_import(self, body, *extra):
        path = Path(self.dir) / "p.csv"
        path.write_text(self.HEADER + body, encoding="utf-8-sig")
        out, err = StringIO(), StringIO()
        call_command("import_providers", str(path), "--city", "kr", *extra, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_create_update_and_warnings(self):
        out, err = self.run_import(
            'СТО Альфа,sto,Інгулецький,"вул. Тестова, 1",+380671112233,@alfa,,Пн-Пт 9-18,Toyota;Honda,Instagram\n'
            "СТО Бета,sto,,,,,,,,\n"
            "Невідоме,xxx,,,,,,,,\n"
            ",sto,,,,,,,,\n")
        self.assertIn("Створено: 2", out)
        self.assertIn("невідома категорія «xxx»", err)
        self.assertIn("порожня назва", err)
        alfa = Provider.objects.get(name="СТО Альфа")
        self.assertEqual(alfa.address, "вул. Тестова, 1")
        self.assertEqual(sorted(alfa.brands.values_list("name", flat=True)), ["Honda", "Toyota"])
        self.assertEqual(alfa.data_status, Provider.DataStatus.COLLECTED)
        self.assertEqual(alfa.source_note, "Instagram")
        self.assertFalse(Provider.objects.filter(name="Невідоме").exists())

    def test_reimport_is_idempotent_and_keeps_existing_values(self):
        self.run_import("СТО Альфа,sto,,,+380671112233,,,,,\n")
        out, _ = self.run_import("СТО Альфа,sto,Тернівський,,,,,,,\n")
        self.assertIn("Створено: 0, оновлено: 1", out)
        alfa = Provider.objects.get(name="СТО Альфа")
        self.assertEqual(alfa.district, "Тернівський")
        self.assertEqual(alfa.phone, "+380671112233", "порожнє значення не затирає наявне")
        self.assertEqual(Provider.objects.filter(name="СТО Альфа").count(), 1)

    def test_dry_run_saves_nothing(self):
        out, _ = self.run_import("СТО Гамма,sto,,,,,,,,\n", "--dry-run")
        self.assertIn("dry-run", out)
        self.assertFalse(Provider.objects.filter(name="СТО Гамма").exists())

    def test_bad_inputs(self):
        path = Path(self.dir) / "bad.csv"
        path.write_text("title,cats\nx,y\n", encoding="utf-8")
        with self.assertRaises(CommandError):
            call_command("import_providers", str(path), "--city", "kr", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("import_providers", str(Path(self.dir) / "nope.csv"), stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("import_providers", str(path), "--city", "unknown-city", stdout=StringIO())
