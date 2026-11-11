"""Бізнес-логіка заявок і угод (розділ 7 ТЗ).

Усі переходи статусів проходять тут, щоб кожна зміна потрапляла в журнал
із відповідальним і часом.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, ExpressionWrapper, F, DurationField
from django.utils import timezone

from apps.catalog.models import Provider
from apps.core.notifications import notify, notify_liaisons

from .models import BypassReport, Offer, Order, OrderStatusLog

S = Order.Status

# Дозволені переходи між статусами
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    S.NEW: {S.OFFERED, S.BOOKED, S.CANCELLED},
    S.OFFERED: {S.BOOKED, S.NEW, S.CANCELLED},
    S.BOOKED: {S.VISITED, S.CANCELLED},
    S.VISITED: {S.ESTIMATE, S.CANCELLED},
    S.ESTIMATE: {S.DONE, S.CANCELLED},
    S.DONE: {S.REVIEWED},
    S.REVIEWED: set(),
    S.CANCELLED: set(),
}


class OrderError(ValueError):
    """Порушення правил переходу між статусами."""


def _log(order: Order, from_status: str, to_status: str, by, note: str = "") -> None:
    OrderStatusLog.objects.create(
        order=order, from_status=from_status, to_status=to_status, changed_by=by, note=note[:255]
    )


@transaction.atomic
def change_status(order: Order, new_status: str, by=None, note: str = "") -> Order:
    old = order.status
    if new_status == old:
        return order
    if new_status not in ALLOWED_TRANSITIONS.get(old, set()):
        raise OrderError(f"Перехід «{old}» → «{new_status}» не дозволений")
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    _log(order, old, new_status, by, note)
    _notify_status(order, new_status)
    return order


def _money(lo, hi) -> str:
    if lo is None and hi is None:
        return "ціну уточнюйте"
    if lo is None or hi is None or lo == hi:
        value = lo if lo is not None else hi
        return f"{value:.0f} грн"
    return f"{lo:.0f}–{hi:.0f} грн"


def _notify_status(order: Order, status: str) -> None:
    """Сповіщення сторонам про ключові зміни (Telegram). Решта змін видима в журналі заявки."""
    provider = order.provider
    owner = provider.owner if provider and provider.owner_id else None
    if status == Order.Status.BOOKED and provider:
        when = order.scheduled_at.strftime("%d.%m.%Y %H:%M") if order.scheduled_at else "час уточнюється"
        contacts = "\n".join(filter(None, [
            f"Телефон: {provider.phone}" if provider.phone else "",
            f"Telegram: {provider.telegram_username}" if provider.telegram_username else "",
            f"Адреса: {provider.address}" if provider.address else "",
        ]))
        notify(order.client,
               f"✅ Запис по заявці №{order.pk}: {provider.name}, {when}.\n{contacts}\n"
               f"Код візиту (покажіть виконавцю): {order.visit_code}",
               kind="booked", payload={"order_id": order.pk})
        notify(owner,
               f"📅 Запис по заявці №{order.pk} на {when}.\nПроблема: {order.description[:300]}\n"
               f"Після візиту введіть код від клієнта: /visit {order.pk} КОД",
               kind="provider_booked", payload={"order_id": order.pk})
    elif status == Order.Status.VISITED:
        notify(order.client, f"Візит по заявці №{order.pk} підтверджено. Очікуйте кошторис від виконавця.",
               kind="visited", payload={"order_id": order.pk})
    elif status == Order.Status.ESTIMATE:
        notify(owner, f"Клієнт погодив кошторис по заявці №{order.pk}. Після завершення робіт: "
                      f"/done {order.pk} СУМА [ДНІВ_ГАРАНТІЇ]", kind="provider_estimate_ok", payload={"order_id": order.pk})
    elif status == Order.Status.DONE:
        notify(order.client, f"🎉 Роботу по заявці №{order.pk} підтверджено обома сторонами. "
                             f"Запис додано в гараж. Оцініть виконавця:",
               kind="review_request", payload={"order_id": order.pk})
        notify(owner, f"Угоду по заявці №{order.pk} підтверджено обома сторонами.", kind="provider_done")
    elif status == Order.Status.CANCELLED:
        notify(order.client, f"Заявку №{order.pk} скасовано.", kind="cancelled")
        notify(owner, f"Заявку №{order.pk} скасовано.", kind="cancelled")


@transaction.atomic
def create_order(*, client, city, description: str, source: str = Order.Source.SITE, vehicle=None,
                 category=None, service_type=None, provider: Provider | None = None,
                 preferred_time: str = "", lead_origin: str = Order.LeadOrigin.PLATFORM) -> Order:
    """Нова заявка. Якщо клієнт одразу обрав виконавця, йому створюється запит на пропозицію."""
    if service_type and not category:
        category = service_type.category
    order = Order.objects.create(
        client=client, city=city, description=description.strip(), source=source, vehicle=vehicle,
        category=category, service_type=service_type, preferred_time=preferred_time,
        lead_origin=lead_origin,
    )
    _log(order, "", Order.Status.NEW, client, "Заявку створено")
    notify_liaisons(
        f"🆕 Нова заявка №{order.pk} ({order.get_source_display()}): {order.description[:200]}\n"
        f"{settings.SITE_URL}/admin/orders/order/{order.pk}/change/",
        kind="new_order", payload={"order_id": order.pk},
    )
    if provider:
        request_offer(order, provider, by=client)
    return order


def log_manual_creation(order: Order, by) -> None:
    """Заявка, створена зв'язковим в адмінці (наприклад, за дзвінком клієнта)."""
    _log(order, "", order.status, by, "Заявку створено зв'язковим")


@transaction.atomic
def request_offer(order: Order, provider: Provider, by=None) -> Offer:
    """Запит пропозиції виконавцю. Зв'язковий може розіслати 2–3 виконавцям."""
    offer, created = Offer.objects.get_or_create(order=order, provider=provider)
    if created:
        _log(order, order.status, order.status, by, f"Запит виконавцю «{provider.name}»")
        car = f"\nАвто: {order.vehicle.display_name}" if order.vehicle_id else ""
        when = f"\nБажаний час: {order.preferred_time}" if order.preferred_time else ""
        notify(
            provider.owner,
            f"🔧 Нова заявка №{order.pk} для «{provider.name}»:\n{order.description[:400]}{car}{when}\n"
            f"Чи берете в роботу?",
            kind="offer_request", payload={"offer_id": offer.pk, "order_id": order.pk},
        )
    return offer


@transaction.atomic
def provider_respond(offer: Offer, *, accept: bool, price_min=None, price_max=None, term_note: str = "",
                     suggested_time: str = "", comment: str = "", by=None) -> Offer:
    """Виконавець відповідає на запит: вилка ціни, строк, час запису або відмова."""
    offer.responded_at = timezone.now()
    if accept:
        offer.status = Offer.Status.PROPOSED
        offer.price_min, offer.price_max = price_min, price_max
        offer.term_note, offer.suggested_time, offer.comment = term_note, suggested_time, comment
    else:
        offer.status = Offer.Status.DECLINED
        offer.comment = comment
    offer.save()
    order = offer.order
    if accept and order.status == Order.Status.NEW:
        change_status(order, Order.Status.OFFERED, by=by, note=f"Пропозиція від «{offer.provider.name}»")
    else:
        _log(order, order.status, order.status, by, f"«{offer.provider.name}»: {'пропозиція' if accept else 'відмова'}")
    if accept:
        extras = ", ".join(filter(None, [f"строк: {offer.term_note}" if offer.term_note else "",
                                         f"час: {offer.suggested_time}" if offer.suggested_time else ""]))
        notify(order.client,
               f"💬 «{offer.provider.name}» пропонує по заявці №{order.pk}: "
               f"{_money(offer.price_min, offer.price_max)}{'; ' + extras if extras else ''}.",
               kind="offer_proposed", payload={"offer_id": offer.pk, "order_id": order.pk})
    else:
        notify_liaisons(f"❌ «{offer.provider.name}» відмовився від заявки №{order.pk}. Підберіть іншого виконавця.",
                        kind="offer_declined", payload={"order_id": order.pk})
    _update_provider_response_time(offer.provider)
    return offer


def _update_provider_response_time(provider: Provider) -> None:
    rows = Offer.objects.filter(provider=provider, responded_at__isnull=False).annotate(
        delta=ExpressionWrapper(F("responded_at") - F("created_at"), output_field=DurationField())
    ).aggregate(avg=Avg("delta"))
    avg = rows["avg"]
    provider.avg_response_minutes = int(avg.total_seconds() // 60) if avg is not None else None
    provider.save(update_fields=["avg_response_minutes", "updated_at"])


@transaction.atomic
def book_visit(order: Order, offer: Offer, scheduled_at: datetime | None, by=None) -> Order:
    """Клієнт (або зв'язковий) обирає пропозицію і фіксує запис. Контакти відкриваються після цього."""
    if offer.order_id != order.pk:
        raise OrderError("Пропозиція належить іншій заявці")
    if offer.status not in {Offer.Status.PROPOSED, Offer.Status.REQUESTED}:
        raise OrderError("Цю пропозицію вже не можна обрати")
    order.provider = offer.provider
    order.price_min, order.price_max = offer.price_min, offer.price_max
    order.term_note = offer.term_note
    order.scheduled_at = scheduled_at
    order.visit_code = Order.new_visit_code()
    order.save()
    offer.status = Offer.Status.ACCEPTED
    offer.save(update_fields=["status", "updated_at"])
    order.offers.exclude(pk=offer.pk).update(status=Offer.Status.REJECTED)
    change_status(order, Order.Status.BOOKED, by=by, note=f"Запис у «{offer.provider.name}»")
    return order


@transaction.atomic
def confirm_visit(order: Order, code: str, by=None) -> Order:
    """Підтвердження візиту кодом (клієнт показує код виконавцю)."""
    if order.status != Order.Status.BOOKED:
        raise OrderError("Візит можна підтвердити лише для заявки зі статусом «Запис»")
    if not order.visit_code or code.strip() != order.visit_code:
        raise OrderError("Невірний код підтвердження візиту")
    order.visit_confirmed_at = timezone.now()
    order.save(update_fields=["visit_confirmed_at", "updated_at"])
    return change_status(order, Order.Status.VISITED, by=by, note="Візит підтверджено кодом")


@transaction.atomic
def propose_estimate(order: Order, text: str, amount: Decimal, by=None) -> Order:
    """Виконавець (або зв'язковий) вносить кошторис; клієнт погоджує його окремим кроком."""
    if order.status != Order.Status.VISITED:
        raise OrderError("Кошторис вноситься після візиту")
    order.estimate_text, order.estimate_amount = text, amount
    order.estimate_approved_at = None
    order.save(update_fields=["estimate_text", "estimate_amount", "estimate_approved_at", "updated_at"])
    _log(order, order.status, order.status, by, f"Кошторис: {amount} грн")
    notify(order.client, f"🧾 Кошторис по заявці №{order.pk}: {amount} грн.\n{text[:500]}",
           kind="estimate", payload={"order_id": order.pk})
    return order


@transaction.atomic
def approve_estimate(order: Order, by=None) -> Order:
    if order.status != Order.Status.VISITED or order.estimate_amount is None:
        raise OrderError("Немає кошторису для погодження")
    order.estimate_approved_at = timezone.now()
    order.save(update_fields=["estimate_approved_at", "updated_at"])
    return change_status(order, Order.Status.ESTIMATE, by=by, note="Клієнт погодив кошторис")


@transaction.atomic
def confirm_work(order: Order, *, side: str, by=None, final_amount: Decimal | None = None,
                 warranty_days: int | None = None) -> Order:
    """Підтвердження виконаної роботи кожною стороною. Коли підтвердили обидві, настає угода."""
    if order.status != Order.Status.ESTIMATE:
        raise OrderError("Роботу можна підтвердити після погодження кошторису")
    now = timezone.now()
    if side == BypassReport.Side.PROVIDER:
        order.provider_confirmed_at = now
        if final_amount is not None:
            order.final_amount = final_amount
        if warranty_days is not None:
            order.warranty_days = warranty_days
    elif side == BypassReport.Side.CLIENT:
        order.client_confirmed_at = now
    else:
        raise OrderError("Невідома сторона")
    order.save()
    _log(order, order.status, order.status, by, f"Роботу підтвердив: {BypassReport.Side(side).label.lower()}")
    if not order.is_confirmed_deal:
        if side == BypassReport.Side.PROVIDER:
            amount = f" на {order.final_amount:.0f} грн" if order.final_amount is not None else ""
            notify(order.client, f"🛠 Виконавець повідомив, що роботу по заявці №{order.pk} виконано{amount}. "
                                 f"Підтвердьте, якщо все так.", kind="confirm_work", payload={"order_id": order.pk})
        else:
            owner = order.provider.owner if order.provider_id and order.provider.owner_id else None
            notify(owner, f"Клієнт підтвердив виконання заявки №{order.pk}. Підтвердьте і ви: "
                          f"/done {order.pk} СУМА [ДНІВ_ГАРАНТІЇ]", kind="provider_confirm_work")
    if order.is_confirmed_deal:
        if order.final_amount is None:
            order.final_amount = order.estimate_amount
            order.save(update_fields=["final_amount", "updated_at"])
        change_status(order, Order.Status.DONE, by=by, note="Роботу підтвердили обидві сторони")
        _on_deal_confirmed(order)
    return order


def _on_deal_confirmed(order: Order) -> None:
    """Угода → запис у гаражі «підтверджено виконавцем» + лічильник виконавця."""
    from apps.garage.services import add_confirmed_record_from_order

    Provider.objects.filter(pk=order.provider_id).update(
        confirmed_deals_count=F("confirmed_deals_count") + 1
    )
    if order.vehicle_id:
        add_confirmed_record_from_order(order)


@transaction.atomic
def cancel_order(order: Order, reason: str = "", by=None) -> Order:
    order.cancel_reason = reason[:255]
    order.save(update_fields=["cancel_reason", "updated_at"])
    return change_status(order, Order.Status.CANCELLED, by=by, note=reason)


@transaction.atomic
def report_bypass(order: Order, *, side: str, reason: str, note: str = "", by=None) -> BypassReport:
    return BypassReport.objects.create(order=order, side=side, reason=reason, note=note, reported_by=by)


def pilot_metrics(city=None) -> dict:
    """Метрики пілота (розділ 12 ТЗ), які рахуються з журналу заявок."""
    orders = Order.objects.all()
    if city:
        orders = orders.filter(city=city)
    total = orders.count()
    confirmed = orders.filter(provider_confirmed_at__isnull=False, client_confirmed_at__isnull=False).count()
    offers = Offer.objects.filter(order__in=orders)
    responded = offers.filter(responded_at__isnull=False)
    delta = responded.annotate(
        d=ExpressionWrapper(F("responded_at") - F("created_at"), output_field=DurationField())
    ).aggregate(avg=Avg("d"))["avg"]
    by_status = dict(orders.values_list("status").annotate(c=Count("id")))
    bypass = dict(BypassReport.objects.filter(order__in=orders).values_list("reason").annotate(c=Count("id")))
    return {
        "total_orders": total,
        "confirmed_deals": confirmed,
        "conversion_percent": round(confirmed * 100 / total, 1) if total else 0.0,
        "offers_total": offers.count(),
        "offers_responded": responded.count(),
        "avg_response_minutes": int(delta.total_seconds() // 60) if delta is not None else None,
        "by_status": by_status,
        "bypass_by_reason": bypass,
    }
