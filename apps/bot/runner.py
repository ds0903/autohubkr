"""Запуск бота: long polling + фонові задачі (доставка сповіщень, нагадування)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.conf import settings

from . import logic, reminders
from .handlers import router

log = logging.getLogger(__name__)

DELIVER_EVERY = 5          # секунд
REMINDERS_EVERY = 10 * 60  # секунд
MAINTENANCE_EVERY = 24 * 60 * 60


def keyboard_for(kind: str, payload: dict) -> InlineKeyboardMarkup | None:
    """Кнопки до сповіщення залежно від його типу."""
    def mk(*rows):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
        ])

    if kind == "offer_request" and payload.get("offer_id"):
        oid = payload["offer_id"]
        return mk([("✅ Беру, вказати ціну", f"offer_yes:{oid}"), ("Відмовитись", f"offer_no:{oid}")])
    if kind == "offer_proposed" and payload.get("offer_id"):
        return mk([("Записатись", f"book:{payload['offer_id']}")])
    if kind == "estimate" and payload.get("order_id"):
        return mk([("Погоджую кошторис", f"est:{payload['order_id']}")])
    if kind == "confirm_work" and payload.get("order_id"):
        return mk([("Підтверджую виконання", f"cfm:{payload['order_id']}")])
    if kind == "review_request" and payload.get("order_id"):
        oid = payload["order_id"]
        return mk([(f"{n} ★", f"rate:{oid}:{n}") for n in range(1, 6)])
    return None


async def _run_in_thread(fn, *args, **kwargs):
    return await sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)


async def deliver_pending(bot, run=_run_in_thread) -> int:
    """Надсилає сповіщення з черги. Повертає кількість успішно надісланих."""
    items = await run(logic.pending_notifications)
    sent = 0
    for item in items:
        try:
            await bot.send_message(item["chat_id"], item["text"][:4000],
                                   reply_markup=keyboard_for(item["kind"], item["payload"]))
        except TelegramForbiddenError as exc:
            await run(logic.mark_notification, item["id"], sent=False, error=str(exc), permanent=True)
        except Exception as exc:  # noqa: BLE001 — мережа / ліміти Telegram: повторимо пізніше
            log.warning("Notification %s failed: %s", item["id"], exc)
            await run(logic.mark_notification, item["id"], sent=False, error=str(exc))
        else:
            await run(logic.mark_notification, item["id"], sent=True)
            sent += 1
    return sent


async def _loop(name: str, every: int, fn):
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — фонова задача не повинна вбивати бота
            log.exception("Background task %s failed", name)
        await asyncio.sleep(every)


async def main() -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задано в .env")
    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    run_sync = lambda f: sync_to_async(f, thread_sensitive=True)()  # noqa: E731
    tasks = [
        asyncio.create_task(_loop("deliver", DELIVER_EVERY, lambda: deliver_pending(bot))),
        asyncio.create_task(_loop("visit-reminders", REMINDERS_EVERY, lambda: run_sync(reminders.schedule_visit_reminders))),
        asyncio.create_task(_loop("maintenance", MAINTENANCE_EVERY,
                                  lambda: run_sync(reminders.schedule_maintenance_reminders))),
    ]
    try:
        me = await bot.get_me()
        log.info("Bot @%s started", me.username)
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()
