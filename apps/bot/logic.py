"""Синхронна бізнес-логіка бота (чистий Django, без aiogram), тому її можна тестувати без Telegram.

Кожна функція, що змінює дані, перевіряє, що користувач має право на цю заявку / пропозицію.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.assistant import services as assistant
from apps.catalog.models import Provider, ServiceType
from apps.core.models import City, Notification
from apps.garage import services as garage_services
from apps.garage.models import Receipt, Vehicle, vin_validator
from apps.orders import services as order_services
from apps.orders.models import BypassReport, Offer, Order
from apps.orders.services import OrderError
from apps.reviews import services as review_services
from apps.reviews.services import ReviewError

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.I)


# ---------- Користувачі ----------

def get_or_create_tg_user(tg_id: int, first_name: str = "") -> tuple[User, bool]:
    user = User.objects.filter(telegram_id=tg_id).first()
    if user:
        return user, False
    user = User(
        username=f"tg_{tg_id}", first_name=(first_name or "")[:150], telegram_id=tg_id, role=User.Role.CLIENT,
        city=City.objects.filter(is_active=True).order_by("id").first(),
    )
    user.set_unusable_password()
    user.save()
    return user, True


def has_consent(user: User) -> bool:
    return user.personal_data_consent_at is not None


def grant_consent(user: User, *, ai_dialogs: bool) -> None:
    user.personal_data_consent_at = timezone.now()
    user.ai_dialog_consent = ai_dialogs
    user.save(update_fields=["personal_data_consent_at", "ai_dialog_consent"])


def is_provider(user: User) -> bool:
    return user.owned_providers.exists()


# ---------- Помічник та заявки клієнта ----------

def current_vehicle(user: User) -> Vehicle | None:
    return Vehicle.objects.filter(owner=user, is_active=True).order_by("-created_at").first()


def run_ai(user: User, text: str) -> dict:
    vehicle = current_vehicle(user)
    reply, _ = assistant.ask(text, user=user, channel="bot", vehicle=vehicle, city=user.city)
    return {
        "text": reply.as_text(),
        "providers": [(s.provider.pk, s.provider.name, s.is_ad) for s in reply.providers],
        "service_slug": reply.service_type.slug if reply.service_type else "",
        "needs_details": reply.needs_details,
    }


def create_order_for(user: User, description: str, *, provider_id: int | None = None, service_slug: str = "") -> Order:
    provider = Provider.objects.filter(pk=provider_id, is_active=True).first() if provider_id else None
    service_type = ServiceType.objects.filter(slug=service_slug).first() if service_slug else None
    return order_services.create_order(
        client=user, city=user.city or City.objects.filter(is_active=True).first(), description=description,
        vehicle=current_vehicle(user), service_type=service_type, provider=provider, source=Order.Source.BOT,
    )


def _client_order(user: User, order_id: int) -> Order | None:
    return Order.objects.filter(pk=order_id, client=user).select_related("provider").first()


def orders_overview(user: User, limit: int = 8) -> list[dict]:
    rows = []
    for o in Order.objects.filter(client=user).select_related("provider", "service_type").order_by("-created_at")[:limit]:
        lines = [f"№{o.pk} · {o.get_status_display()} · {(o.service_type.name if o.service_type else o.description[:40])}"]
        if o.provider and o.contacts_visible:
            lines.append(f"   {o.provider.name}, тел. {o.provider.phone or '—'}")
            if o.status == Order.Status.BOOKED:
                lines.append(f"   Код візиту: {o.visit_code}")
        buttons: list[tuple[str, str]] = []
        for offer in o.offers.filter(status=Offer.Status.PROPOSED).select_related("provider"):
            if o.status in {Order.Status.NEW, Order.Status.OFFERED}:
                buttons.append((f"Записатись: {offer.provider.name}", f"book:{offer.pk}"))
        if o.status == Order.Status.VISITED and o.estimate_amount is not None:
            buttons.append((f"Погодити кошторис {o.estimate_amount:.0f} грн", f"est:{o.pk}"))
        if o.status == Order.Status.ESTIMATE and not o.client_confirmed_at:
            buttons.append(("Підтвердити виконання", f"cfm:{o.pk}"))
        if o.status not in {Order.Status.DONE, Order.Status.REVIEWED, Order.Status.CANCELLED}:
            buttons.append(("Скасувати", f"cancel:{o.pk}"))
        rows.append({"text": "\n".join(lines), "buttons": buttons})
    return rows


def _guard(fn):
    try:
        return True, fn()
    except (OrderError, ReviewError) as exc:
        return False, str(exc)


def book_offer(user: User, offer_id: int) -> tuple[bool, str]:
    offer = Offer.objects.filter(pk=offer_id, order__client=user).select_related("order", "provider").first()
    if not offer:
        return False, "Пропозицію не знайдено."
    ok, res = _guard(lambda: order_services.book_visit(offer.order, offer, None, by=user))
    return ok, ("Запис створено. Контакти й код візиту надіслано окремим повідомленням." if ok else res)


def approve_estimate(user: User, order_id: int) -> tuple[bool, str]:
    order = _client_order(user, order_id)
    if not order:
        return False, "Заявку не знайдено."
    ok, res = _guard(lambda: order_services.approve_estimate(order, by=user))
    return ok, ("Кошторис погоджено." if ok else res)


def client_confirm(user: User, order_id: int) -> tuple[bool, str]:
    order = _client_order(user, order_id)
    if not order:
        return False, "Заявку не знайдено."
    ok, res = _guard(lambda: order_services.confirm_work(order, side=BypassReport.Side.CLIENT, by=user))
    return ok, ("Дякуємо, виконання підтверджено." if ok else res)


def cancel_order(user: User, order_id: int) -> tuple[bool, str]:
    order = _client_order(user, order_id)
    if not order:
        return False, "Заявку не знайдено."
    ok, res = _guard(lambda: order_services.cancel_order(order, "Скасовано клієнтом у боті", by=user))
    return ok, ("Заявку скасовано." if ok else res)


def rate_order(user: User, order_id: int, rating: int) -> tuple[bool, str]:
    order = _client_order(user, order_id)
    if not order:
        return False, "Заявку не знайдено."
    ok, res = _guard(lambda: review_services.submit_review(order, rating, ""))
    return ok, ("Дякуємо за оцінку! Відгук з'явиться після модерації." if ok else res)


# ---------- Виконавець ----------

NUM = r"(\d[\d\s]*(?:[.,]\d+)?)"


def _num(s: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"\s", "", s).replace(",", "."))
    except InvalidOperation:
        return None


def parse_offer_text(text: str) -> tuple[Decimal | None, Decimal | None, str]:
    """«300-500 грн, 1 день, завтра 10:00» → (300, 500, «1 день, завтра 10:00»)."""
    m = re.search(NUM + r"\s*[-–—]\s*" + NUM, text)
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        rest = (text[: m.start()] + " " + text[m.end():]).strip(" ,;.")
    else:
        m = re.search(NUM, text)
        lo = hi = _num(m.group(1)) if m else None
        rest = (text[: m.start()] + " " + text[m.end():]).strip(" ,;.") if m else text.strip()
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    rest = re.sub(r"^\s*(грн|uah|₴)\.?[,\s]*", "", rest, flags=re.I)
    return lo, hi, re.sub(r"\s+", " ", rest).strip()


def _provider_offer(user: User, offer_id: int) -> Offer | None:
    return Offer.objects.filter(pk=offer_id, provider__owner=user).select_related("order", "provider").first()


def offer_decline(user: User, offer_id: int) -> tuple[bool, str]:
    offer = _provider_offer(user, offer_id)
    if not offer or offer.responded_at:
        return False, "Запит не знайдено або вже оброблений."
    order_services.provider_respond(offer, accept=False, comment="Виконавець відмовився", by=user)
    return True, "Дякуємо, відмову зафіксовано."


def offer_accept(user: User, offer_id: int, text: str) -> tuple[bool, str]:
    offer = _provider_offer(user, offer_id)
    if not offer or offer.responded_at:
        return False, "Запит не знайдено або вже оброблений."
    lo, hi, rest = parse_offer_text(text)
    if lo is None:
        return False, "Не бачу ціни. Напишіть, наприклад: 300-500 грн, 1 день, завтра о 10:00"
    # залишок тексту («1 день, завтра 10:00») зберігаємо один раз: у «строк / час», без дублювання в інших полях
    order_services.provider_respond(offer, accept=True, price_min=lo, price_max=hi, term_note=rest[:120], by=user)
    return True, "Пропозицію надіслано клієнту."


def _provider_order(user: User, order_id: int) -> Order | None:
    return Order.objects.filter(pk=order_id, provider__owner=user).select_related("provider", "client").first()


def provider_visit(user: User, order_id: int, code: str) -> tuple[bool, str]:
    order = _provider_order(user, order_id)
    if not order:
        return False, "Заявку не знайдено серед ваших."
    ok, res = _guard(lambda: order_services.confirm_visit(order, code, by=user))
    return ok, ("Візит підтверджено. Внесіть кошторис: /estimate НОМЕР СУМА опис робіт" if ok else res)


def provider_estimate(user: User, order_id: int, amount: str, text: str) -> tuple[bool, str]:
    order = _provider_order(user, order_id)
    value = _num(amount)
    if not order or value is None or value <= 0:
        return False, "Формат: /estimate НОМЕР СУМА опис робіт"
    ok, res = _guard(lambda: order_services.propose_estimate(order, text or "Роботи за домовленістю", value, by=user))
    return ok, ("Кошторис надіслано клієнту на погодження." if ok else res)


def provider_done(user: User, order_id: int, amount: str | None = None, warranty: str | None = None) -> tuple[bool, str]:
    order = _provider_order(user, order_id)
    if not order:
        return False, "Заявку не знайдено серед ваших."
    final = _num(amount) if amount else None
    days = int(warranty) if warranty and warranty.isdigit() else None
    ok, res = _guard(lambda: order_services.confirm_work(order, side=BypassReport.Side.PROVIDER, by=user,
                                                         final_amount=final, warranty_days=days))
    return ok, ("Виконання підтверджено з вашого боку." if ok else res)


# ---------- Гараж ----------

def add_vehicle_from_text(user: User, text: str) -> Vehicle:
    """VIN (17 знаків) або «Марка Модель Рік»."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("Порожній ввід")
    if VIN_RE.match(text.replace(" ", "")):
        vin = text.replace(" ", "").upper()
        vin_validator(vin)
        if Vehicle.objects.filter(owner=user, vin=vin).exists():
            raise ValueError("Авто з таким VIN уже є у вашому гаражі.")
        vehicle = Vehicle.objects.create(owner=user, vin=vin)
        return garage_services.fill_vehicle_from_vin(vehicle)
    vehicle = garage_services.vehicle_from_text(text, owner=user)
    vehicle.save()
    return vehicle


def vehicles_list(user: User) -> list[Vehicle]:
    return list(Vehicle.objects.filter(owner=user, is_active=True).select_related("brand").order_by("-created_at"))


def plan_text(vehicle: Vehicle) -> str:
    garage_services.ensure_default_rules()
    items, disclaimer = garage_services.maintenance_plan(vehicle)
    label = {"overdue": "⛔ прострочено", "soon": "⚠️ скоро", "ok": "✅ ок", "unknown": "• немає запису"}
    lines = [f"План ТО: {vehicle.display_name}"]
    for i in items:
        due = " / ".join(filter(None, [i.due_date.strftime("%d.%m.%Y") if i.due_date else "",
                                      f"{i.due_km} км" if i.due_km else ""]))
        lines.append(f"{label[i.status]} {i.rule.title}" + (f" (до {due})" if due else ""))
    lines.append(disclaimer)
    return "\n".join(lines)


def expenses_text(vehicle: Vehicle) -> str:
    rows = garage_services.monthly_expenses(vehicle)
    if not rows:
        return "Витрат ще немає. Надішліть фото чека, і я додам запис."
    lines = [f"Витрати: {vehicle.display_name}"]
    for r in rows[:6]:
        kinds = ", ".join(f"{k} {v:.0f}" for k, v in r["by_kind"].items())
        lines.append(f"{r['month']:%m.%Y}: {r['total']:.0f} грн ({kinds})")
    return "\n".join(lines)


def receipt_from_photo(user: User, data: bytes, filename: str = "receipt.jpg") -> tuple[bool, str]:
    vehicle = current_vehicle(user)
    if not vehicle:
        return False, "Спершу додайте авто: /addcar VIN або /addcar Toyota Camry 2015"
    receipt = Receipt(vehicle=vehicle)
    receipt.image.save(filename, ContentFile(data), save=True)
    record = garage_services.process_receipt(receipt)
    if not record:
        return False, "Не вдалося розпізнати чек. Спробуйте чіткіше фото або додайте запис на сайті."
    amount = f"{record.amount:.0f} грн" if record.amount is not None else "сума не визначена"
    return True, (f"Додано в гараж «{vehicle.display_name}»: {record.title}, {record.date:%d.%m.%Y}, {amount}. "
                  f"Позначка: «зі слів власника». Перевірте дані на сайті.")


# ---------- Черга сповіщень ----------

def pending_notifications(limit: int = 20) -> list[dict]:
    qs = Notification.objects.filter(sent_at__isnull=True, attempts__lt=5).select_related("user").order_by("created_at")[:limit]
    return [{"id": n.pk, "chat_id": n.user.telegram_id, "text": n.text, "kind": n.kind, "payload": n.payload} for n in qs
            if n.user.telegram_id]


def mark_notification(notification_id: int, *, sent: bool, error: str = "", permanent: bool = False) -> None:
    n = Notification.objects.filter(pk=notification_id).first()
    if not n:
        return
    if sent:
        n.sent_at = timezone.now()
    else:
        n.attempts = 5 if permanent else n.attempts + 1
        n.error = error[:255]
    n.save(update_fields=["sent_at", "attempts", "error", "updated_at"])
