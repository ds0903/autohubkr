"""Гараж: VIN, запис підтверджених робіт, витрати, план ТО, розпізнавання чеків."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.catalog.models import CarBrand
from apps.core.gemini import make_client

from .models import SERVICE_BOOK_DISCLAIMER, GarageRecord, MaintenanceRule, Receipt, Vehicle

log = logging.getLogger(__name__)


def vehicle_from_text(text: str, owner=None) -> Vehicle:
    """«Toyota Camry 2015» → непзбережений Vehicle: марка зі списку марок, рік окремо, решта в модель.

    Невідому марку в довідник не додаємо (його наповнює адміністратор): вона лишається на початку назви моделі.
    """
    tokens = re.sub(r"\s+", " ", text).strip().split(" ")
    year = None
    rest = []
    for t in tokens:
        if year is None and re.fullmatch(r"(19|20)\d{2}", t):
            year = int(t)
        elif t:
            rest.append(t)
    brand = None
    for size in (2, 1):  # «Land Rover», «Alfa Romeo» — марка з двох слів має перевагу
        if len(rest) >= size:
            brand = CarBrand.objects.filter(name__iexact=" ".join(rest[:size])).first()
            if brand:
                rest = rest[size:]
                break
    return Vehicle(owner=owner, brand=brand, model_name=" ".join(rest)[:80], year=year)


# ---------- VIN ----------

# Перші 3 символи VIN (WMI) → марка. Мінімальна локальна таблиця: безкоштовного
# повного декодера для європейських авто немає, тому не обіцяємо більше, ніж знаємо (розділ 9 ТЗ).
WMI_BRANDS = {
    "WVW": "Volkswagen", "WV1": "Volkswagen", "WV2": "Volkswagen", "WAU": "Audi", "WBA": "BMW", "WBS": "BMW",
    "WDB": "Mercedes-Benz", "WDD": "Mercedes-Benz", "W0L": "Opel", "WF0": "Ford", "VF1": "Renault",
    "VF3": "Peugeot", "VF7": "Citroen", "TMB": "Skoda", "JTD": "Toyota", "JT2": "Toyota", "JTM": "Toyota",
    "SB1": "Toyota", "JHM": "Honda", "JN1": "Nissan", "JMZ": "Mazda", "KMH": "Hyundai", "KNA": "Kia",
    "KND": "Kia", "5YJ": "Tesla", "1FA": "Ford", "1G1": "Chevrolet", "2HG": "Honda", "WP0": "Porsche",
    "YV1": "Volvo", "ZFA": "Fiat", "TRU": "Audi", "WME": "Smart", "XTA": "Lada", "Z8N": "Nissan",
    "U5Y": "Kia", "TMA": "Hyundai", "VSS": "SEAT", "JS2": "Suzuki", "SAL": "Land Rover", "SAJ": "Jaguar",
}

# 10-й символ VIN → рік випуску (цикл 30 років)
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def decode_year(vin: str) -> int | None:
    """Рік за 10-м символом. Для 30-річного циклу обираємо ближчий до сьогодні варіант."""
    if len(vin) != 17 or vin[9] not in _YEAR_CODES:
        return None
    idx = _YEAR_CODES.index(vin[9])
    candidates = [1980 + idx, 2010 + idx]
    current = timezone.now().year
    valid = [y for y in candidates if y <= current + 1]
    return max(valid) if valid else None


@dataclass
class VinInfo:
    brand_name: str | None
    year: int | None


def decode_vin(vin: str) -> VinInfo:
    vin = vin.strip().upper()
    return VinInfo(brand_name=WMI_BRANDS.get(vin[:3]), year=decode_year(vin))


def fill_vehicle_from_vin(vehicle: Vehicle) -> Vehicle:
    """Підставляє марку і рік за VIN, якщо вони не вказані. Решту користувач вводить сам."""
    if not vehicle.vin:
        return vehicle
    info = decode_vin(vehicle.vin)
    changed = False
    if info.brand_name and not vehicle.brand_id:
        vehicle.brand, _ = CarBrand.objects.get_or_create(
            name=info.brand_name, defaults={"slug": info.brand_name.lower().replace(" ", "-")}
        )
        changed = True
    if info.year and not vehicle.year:
        vehicle.year = info.year
        changed = True
    if changed:
        vehicle.data_source = Vehicle.DataSource.VIN_DECODED
        vehicle.save()
    return vehicle


# ---------- Записи гаража ----------

def add_confirmed_record_from_order(order) -> GarageRecord:
    """Робота, виконана через платформу, потрапляє в гараж із позначкою «підтверджено виконавцем»."""
    record, _ = GarageRecord.objects.get_or_create(
        order=order,
        defaults=dict(
            vehicle=order.vehicle,
            kind=GarageRecord.Kind.SERVICE,
            title=(order.service_type.name if order.service_type else order.description[:80]),
            works=order.estimate_text,
            date=timezone.localdate(),
            amount=order.final_amount,
            trust=GarageRecord.Trust.PROVIDER_CONFIRMED,
            provider=order.provider,
        ),
    )
    return record


def monthly_expenses(vehicle: Vehicle) -> list[dict]:
    """Зведення витрат по місяцях і типах."""
    rows = (
        vehicle.records.exclude(amount__isnull=True)
        .annotate(month=TruncMonth("date"))
        .values("month", "kind")
        .annotate(total=Sum("amount"))
        .order_by("-month", "kind")
    )
    labels = dict(GarageRecord.Kind.choices)
    out: dict = {}
    for r in rows:
        bucket = out.setdefault(r["month"], {"month": r["month"], "total": Decimal("0"), "by_kind": {}})
        bucket["total"] += r["total"]
        bucket["by_kind"][labels[r["kind"]]] = r["total"]
    return list(out.values())


# ---------- План ТО ----------

DEFAULT_RULES = [
    ("Заміна моторного мастила і масляного фільтра", 10000, 12, ""),
    ("Заміна повітряного фільтра", 20000, 24, ""),
    ("Заміна салонного фільтра", 15000, 12, ""),
    ("Заміна паливного фільтра", 30000, 36, "залежить від типу двигуна"),
    ("Заміна гальмівних колодок (перевірка зносу)", 30000, None, "орієнтир, залежить від стилю їзди"),
    ("Заміна гальмівної рідини", None, 24, ""),
    ("Заміна свічок запалювання", 40000, 48, "для бензинових двигунів"),
    ("Заміна охолоджувальної рідини", 60000, 60, ""),
    ("Діагностика ходової частини", 20000, 12, ""),
    ("Заміна ременя / ланцюга ГРМ", 90000, 72, "точний інтервал дивіться в сервісній книжці"),
]


def ensure_default_rules() -> int:
    """Створює універсальні нормативи, якщо їх ще немає. Повертає кількість створених."""
    created = 0
    for title, km, months, note in DEFAULT_RULES:
        _, was_created = MaintenanceRule.objects.get_or_create(
            title=title, brand=None, defaults=dict(interval_km=km, interval_months=months, note=note)
        )
        created += int(was_created)
    return created


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # останній день місяця, якщо день не вміщується
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass
class PlanItem:
    rule: MaintenanceRule
    last_done_on: date | None
    last_done_km: int | None
    due_date: date | None
    due_km: int | None
    status: str  # "overdue" | "soon" | "ok" | "unknown"
    note: str


def maintenance_plan(vehicle: Vehicle, today: date | None = None) -> tuple[list[PlanItem], str]:
    """План ТО за пробігом і часом із загальних нормативів + застереження про сервісну книжку."""
    today = today or timezone.localdate()
    rules = MaintenanceRule.objects.filter(is_active=True).filter(
        brand__isnull=True
    ) | MaintenanceRule.objects.filter(is_active=True, brand_id=vehicle.brand_id)
    items: list[PlanItem] = []
    for rule in rules.distinct().order_by("title"):
        last = vehicle.records.filter(rule=rule).order_by("-date").first()
        due_date = due_km = None
        if last:
            if rule.interval_months:
                due_date = _add_months(last.date, rule.interval_months)
            if rule.interval_km and last.mileage is not None:
                due_km = last.mileage + rule.interval_km
        status = "unknown"
        if last:
            status = "ok"
            km_left = (due_km - vehicle.mileage) if (due_km is not None and vehicle.mileage is not None) else None
            days_left = (due_date - today).days if due_date else None
            if (km_left is not None and km_left <= 0) or (days_left is not None and days_left <= 0):
                status = "overdue"
            elif (km_left is not None and km_left <= 1000) or (days_left is not None and days_left <= 30):
                status = "soon"
        items.append(PlanItem(rule, last.date if last else None, last.mileage if last else None,
                              due_date, due_km, status, rule.note))
    return items, SERVICE_BOOK_DISCLAIMER


# ---------- Розпізнавання чеків (Gemini) ----------

RECEIPT_PROMPT = (
    "Ти розпізнаєш фото чека або заказ-наряду автосервісу чи магазину. "
    "Поверни ТІЛЬКИ JSON без пояснень з полями: "
    '{"date": "YYYY-MM-DD" або null, "mileage": ціле число км або null, "total": число або null, '
    '"title": коротка назва (до 80 символів), "works": [список виконаних робіт], '
    '"parts": [список запчастин], "kind": одне з "service","parts","fuel","insurance","other"}. '
    "Якщо поля не видно, став null. Нічого не вигадуй."
)


def parse_receipt_with_gemini(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    from google.genai import types

    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задано")
    client = make_client()
    resp = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), RECEIPT_PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    return json.loads(resp.text)


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01")) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _to_date(value) -> date:
    try:
        return date.fromisoformat(str(value)) if value else timezone.localdate()
    except ValueError:
        return timezone.localdate()


def record_from_parsed(receipt: Receipt, parsed: dict) -> GarageRecord:
    kind = parsed.get("kind") if parsed.get("kind") in dict(GarageRecord.Kind.choices) else GarageRecord.Kind.SERVICE
    mileage = parsed.get("mileage")
    record = GarageRecord.objects.create(
        vehicle=receipt.vehicle,
        kind=kind,
        title=(parsed.get("title") or "Запис з чека")[:200],
        works="\n".join(map(str, parsed.get("works") or [])),
        parts="\n".join(map(str, parsed.get("parts") or [])),
        date=_to_date(parsed.get("date")),
        mileage=int(mileage) if isinstance(mileage, (int, float)) else None,
        amount=_to_decimal(parsed.get("total")),
        trust=GarageRecord.Trust.OWNER_CLAIMED,
        receipt=receipt,
    )
    return record


def process_receipt(receipt: Receipt, parse=None) -> GarageRecord | None:
    """Розпізнає чек і створює запис гаража. Помилки не губляться: статус `failed` + текст помилки."""
    parse = parse or parse_receipt_with_gemini
    try:
        with receipt.image.open("rb") as fh:
            data = fh.read()
        name = receipt.image.name.lower()
        mime = "image/png" if name.endswith(".png") else "image/webp" if name.endswith(".webp") else "image/jpeg"
        parsed = parse(data, mime)
        record = record_from_parsed(receipt, parsed)
    except Exception as exc:  # noqa: BLE001 — зовнішній API / формат відповіді може бути будь-яким
        log.exception("Receipt %s failed", receipt.pk)
        receipt.status = Receipt.Status.FAILED
        receipt.error = str(exc)[:255]
        receipt.save(update_fields=["status", "error", "updated_at"])
        return None
    receipt.status = Receipt.Status.DONE
    receipt.parsed = parsed
    receipt.save(update_fields=["status", "parsed", "updated_at"])
    return record
