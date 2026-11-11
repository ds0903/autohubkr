"""Нагадування клієнтам і виконавцям (кладуть сповіщення в чергу з дедуплікацією)."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.core.notifications import notify
from apps.garage import services as garage_services
from apps.garage.models import Vehicle
from apps.orders.models import Order

VISIT_REMIND_BEFORE = timedelta(hours=3)


def schedule_visit_reminders(now=None) -> int:
    """За 3 години до візиту нагадуємо клієнту і виконавцю (один раз на заявку)."""
    now = now or timezone.now()
    created = 0
    orders = Order.objects.filter(
        status=Order.Status.BOOKED, scheduled_at__gt=now, scheduled_at__lte=now + VISIT_REMIND_BEFORE
    ).select_related("client", "provider__owner")
    for o in orders:
        when = timezone.localtime(o.scheduled_at).strftime("%d.%m.%Y %H:%M")
        created += bool(notify(o.client, f"⏰ Нагадування: візит до «{o.provider.name}» {when}. Код візиту: {o.visit_code}",
                               kind="visit_reminder", dedupe_key=f"visit:{o.pk}:client"))
        created += bool(notify(o.provider.owner, f"⏰ Нагадування: заявка №{o.pk}, візит клієнта {when}.",
                               kind="visit_reminder_provider", dedupe_key=f"visit:{o.pk}:provider"))
    return created


def schedule_maintenance_reminders(today=None) -> int:
    """Нагадування про ТО за пробігом і часом із загальних нормативів. Одне на кожен термін."""
    today = today or timezone.localdate()
    created = 0
    vehicles = Vehicle.objects.filter(is_active=True, owner__telegram_id__isnull=False).select_related("owner", "brand")
    for v in vehicles:
        items, disclaimer = garage_services.maintenance_plan(v, today=today)
        for item in items:
            if item.status not in {"overdue", "soon"}:
                continue
            word = "прострочено" if item.status == "overdue" else "скоро термін"
            key = f"to:{v.pk}:{item.rule.pk}:{item.due_date or ''}:{item.due_km or ''}:{item.status}"
            created += bool(notify(
                v.owner, f"🔧 {v.display_name}: {item.rule.title}, {word}. {disclaimer}",
                kind="maintenance", dedupe_key=key[:120],
            ))
    return created
