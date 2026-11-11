"""Імпорт виконавців з CSV (ручний збір з відкритих джерел, розділ 9 ТЗ).

Колонки (заголовок обов'язковий, кодування UTF-8):
  name; categories; district; address; phone; telegram; website; working_hours; brands; source
categories і brands: через «;» (slug категорій, назви марок). Ідемпотентно: виконавець ідентифікується парою (місто, назва).
"""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import CarBrand, Category, Provider
from apps.core.models import City

REQUIRED = {"name", "categories"}


def split_list(value: str) -> list[str]:
    return [p.strip() for p in (value or "").split(";") if p.strip()]


class Command(BaseCommand):
    help = "Імпортує виконавців з CSV (статус даних: «Зібрано»)."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)
        parser.add_argument("--city", default="kryvyi-rih", help="slug міста (за замовчуванням kryvyi-rih)")
        parser.add_argument("--delimiter", default=",")
        parser.add_argument("--dry-run", action="store_true", help="Лише перевірити файл, нічого не зберігаючи.")

    def handle(self, *args, **opts):
        path: Path = opts["path"]
        if not path.exists():
            raise CommandError(f"Файл не знайдено: {path}")
        city = City.objects.filter(slug=opts["city"]).first()
        if not city:
            raise CommandError(f"Місто «{opts['city']}» не знайдено. Спершу: manage.py seed_initial")

        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=opts["delimiter"])
            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"У файлі немає обов'язкових колонок: {', '.join(sorted(missing))}")
            rows = list(reader)

        created = updated = 0
        errors: list[str] = []
        with transaction.atomic():
            for line, row in enumerate(rows, start=2):
                name = (row.get("name") or "").strip()
                if not name:
                    errors.append(f"рядок {line}: порожня назва")
                    continue
                categories = []
                for slug in split_list(row.get("categories", "")):
                    cat = Category.objects.filter(slug=slug).first()
                    if cat:
                        categories.append(cat)
                    else:
                        errors.append(f"рядок {line} («{name}»): невідома категорія «{slug}»")
                if not categories:
                    errors.append(f"рядок {line} («{name}»): немає жодної відомої категорії, пропущено")
                    continue
                fields = dict(
                    district=(row.get("district") or "").strip(), address=(row.get("address") or "").strip(),
                    phone=(row.get("phone") or "").strip(), telegram_username=(row.get("telegram") or "").strip(),
                    website=(row.get("website") or "").strip(), working_hours=(row.get("working_hours") or "").strip(),
                    source_note=(row.get("source") or "").strip()[:255],
                )
                provider, was_created = Provider.objects.get_or_create(city=city, name=name, defaults=fields)
                if was_created:
                    created += 1
                else:
                    for key, value in fields.items():
                        if value:  # порожні значення не затирають існуючі дані
                            setattr(provider, key, value)
                    provider.save()
                    updated += 1
                provider.categories.set(categories)
                brands = []
                for brand_name in split_list(row.get("brands", "")):
                    brand, _ = CarBrand.objects.get_or_create(
                        name=brand_name, defaults={"slug": brand_name.lower().replace(" ", "-")})
                    brands.append(brand)
                provider.brands.set(brands)
            if opts["dry_run"]:
                transaction.set_rollback(True)

        for e in errors:
            self.stderr.write(self.style.WARNING(e))
        mode = " (dry-run, нічого не збережено)" if opts["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(f"Створено: {created}, оновлено: {updated}, попереджень: {len(errors)}{mode}"))
