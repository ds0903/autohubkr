"""Створення сповіщень. Надсилає їх Telegram-бот (apps.bot), тут лише черга."""
from __future__ import annotations

from django.db import IntegrityError, transaction

from .models import Notification


def notify(user, text: str, *, kind: str = "info", payload: dict | None = None,
           dedupe_key: str | None = None) -> Notification | None:
    """Ставить сповіщення в чергу. Користувачі без Telegram пропускаються мовчки."""
    if user is None or not getattr(user, "telegram_id", None):
        return None
    try:
        with transaction.atomic():
            return Notification.objects.create(
                user=user, kind=kind, text=text, payload=payload or {}, dedupe_key=dedupe_key
            )
    except IntegrityError:  # такий dedupe_key уже є: це повтор
        return None


def notify_liaisons(text: str, *, kind: str = "liaison", payload: dict | None = None) -> int:
    from apps.accounts.models import User

    count = 0
    for user in User.objects.filter(role__in=[User.Role.LIAISON, User.Role.ADMIN], telegram_id__isnull=False, is_active=True):
        count += bool(notify(user, text, kind=kind, payload=payload))
    return count
