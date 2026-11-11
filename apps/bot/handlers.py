"""Telegram-бот (aiogram 3). Тонкі обробники: уся логіка в apps.bot.logic."""
from __future__ import annotations

import io
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError

from apps.accounts import linking

from . import logic

log = logging.getLogger(__name__)
router = Router()


class Flow(StatesGroup):
    offer_reply = State()   # виконавець пише вилку ціни
    add_car = State()       # клієнт вводить VIN або «марка модель рік»


def run(fn, *args, **kwargs):
    """Виклик синхронної Django-логіки з async-обробників."""
    return sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


HELP = (
    "Я допомагаю знайти автосервіс у Кривому Розі.\n\n"
    "Просто опишіть проблему з авто, і я підкажу, що це може бути, яка послуга потрібна й до кого звернутись.\n\n"
    "/orders: мої заявки\n/garage: мій гараж\n/addcar: додати авто (VIN або «Toyota Camry 2015»)\n"
    "/plan: план ТО\n/expenses: витрати\n/link КОД: підключити акаунт із сайту\n/myid: мій Telegram ID\n"
    "Фото чека або заказ-наряду: додам запис у гараж.\n\n"
    "Для виконавців: /visit НОМЕР КОД · /estimate НОМЕР СУМА опис · /done НОМЕР [СУМА] [ДНІВ_ГАРАНТІЇ]"
)

CONSENT_TEXT = (
    "Щоб користуватись ботом, потрібна згода на обробку персональних даних (ім'я, дані про авто, опис проблем, фото чеків). "
    "Мінімум даних, без держномера. Політика: /privacy\n\n"
    "Зберігати ваші діалоги з ШІ для покращення підбору? Це необов'язково."
)


async def ensure_user(message: Message | CallbackQuery):
    """Повертає користувача або просить згоду. None, якщо згоди ще немає."""
    tg = message.from_user
    user, _ = await run(logic.get_or_create_tg_user, tg.id, tg.first_name or "")
    if not await run(logic.has_consent, user):
        target = message.message if isinstance(message, CallbackQuery) else message
        await target.answer(CONSENT_TEXT, reply_markup=kb([
            [("Погоджуюсь + зберігати діалоги", "consent:ai")],
            [("Погоджуюсь, діалоги не зберігати", "consent:noai")],
        ]))
        return None
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    # кнопка «Підключити в Telegram» на сайті відкриває бота з кодом: t.me/<бот>?start=link-<код>
    if command.args and command.args.startswith(linking.START_PREFIX):
        ok, text = await run(linking.link_telegram, message.from_user.id, command.args)
        await message.answer(("✅ " if ok else "⚠️ ") + text + ("\n\n" + HELP if ok else ""))
        return
    user = await ensure_user(message)
    if user:
        await message.answer("Вітаю! " + HELP)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message):
    await message.answer("Ми зберігаємо мінімум даних: ім'я, авто, заявки, записи гаража. Діалоги з ШІ зберігаються лише за "
                         "вашою згодою. Контакти виконавця відкриваються після підтвердження запису. "
                         "Видалення даних: зверніться до зв'язкового.")


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}\n(Виконавцям: передайте його зв'язковому, щоб вам "
                         f"приходили заявки.)")


@router.callback_query(F.data.startswith("consent:"))
async def cb_consent(call: CallbackQuery):
    user, _ = await run(logic.get_or_create_tg_user, call.from_user.id, call.from_user.first_name or "")
    await run(logic.grant_consent, user, ai_dialogs=call.data.endswith(":ai"))
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Дякую! " + HELP)
    await call.answer()


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Скопіюйте команду з вашого профілю на сайті: /link КОД")
        return
    ok, text = await run(linking.link_telegram, message.from_user.id, command.args)
    await message.answer(("✅ " if ok else "⚠️ ") + text)


# ---------- Клієнт: ШІ та заявки ----------

@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext):
    user = await ensure_user(message)
    if not user:
        return
    text = message.text.strip()
    if len(text) < 5:
        await message.answer("Опишіть проблему трохи докладніше, будь ласка.")
        return
    await message.bot.send_chat_action(message.chat.id, "typing")
    result = await run(logic.run_ai, user, text)
    if result["needs_details"]:
        # не про авто / незрозуміло: без кнопок заявки, просимо описати симптоми
        await message.answer(result["text"][:3900])
        return
    await state.update_data(last_query=text, last_service=result["service_slug"])
    rows = [[(f"Заявка: {name}" + (" (реклама)" if ad else ""), f"order:{pid}")] for pid, name, ad in result["providers"]]
    rows.append([("Заявка без вибору: підбере зв'язковий", "order:0")])
    await message.answer(result["text"][:3900], reply_markup=kb(rows))


@router.callback_query(F.data.startswith("order:"))
async def cb_order(call: CallbackQuery, state: FSMContext):
    user = await ensure_user(call)
    if not user:
        await call.answer()
        return
    data = await state.get_data()
    description = data.get("last_query")
    if not description:
        await call.answer("Спершу опишіть проблему текстом.", show_alert=True)
        return
    provider_id = int(call.data.split(":")[1]) or None
    order = await run(logic.create_order_for, user, description, provider_id=provider_id,
                      service_slug=data.get("last_service", ""))
    await state.update_data(last_query=None)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Заявку №{order.pk} створено. Узгодимо ціну й час із виконавцем і повідомимо вас тут. "
                              f"Статуси: /orders")
    await call.answer()


@router.message(Command("orders"))
async def cmd_orders(message: Message):
    user = await ensure_user(message)
    if not user:
        return
    rows = await run(logic.orders_overview, user)
    if not rows:
        await message.answer("Заявок поки немає. Опишіть проблему з авто, і ми почнемо.")
        return
    for row in rows:
        markup = kb([[b] for b in row["buttons"]]) if row["buttons"] else None
        await message.answer(row["text"], reply_markup=markup)


async def _simple_action(call: CallbackQuery, fn, *args):
    user = await ensure_user(call)
    if not user:
        await call.answer()
        return
    ok, text = await run(fn, user, *args)
    await call.answer(text[:190], show_alert=not ok)
    if ok:
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer("✅ " + text)


@router.callback_query(F.data.startswith("book:"))
async def cb_book(call: CallbackQuery):
    await _simple_action(call, logic.book_offer, int(call.data.split(":")[1]))


@router.callback_query(F.data.startswith("est:"))
async def cb_estimate(call: CallbackQuery):
    await _simple_action(call, logic.approve_estimate, int(call.data.split(":")[1]))


@router.callback_query(F.data.startswith("cfm:"))
async def cb_confirm(call: CallbackQuery):
    await _simple_action(call, logic.client_confirm, int(call.data.split(":")[1]))


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery):
    await _simple_action(call, logic.cancel_order, int(call.data.split(":")[1]))


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(call: CallbackQuery):
    _, order_id, rating = call.data.split(":")
    await _simple_action(call, logic.rate_order, int(order_id), int(rating))


# ---------- Виконавець ----------

@router.callback_query(F.data.startswith("offer_no:"))
async def cb_offer_no(call: CallbackQuery):
    user = await ensure_user(call)
    if user:
        ok, text = await run(logic.offer_decline, user, int(call.data.split(":")[1]))
        await call.answer(text, show_alert=not ok)
        if ok:
            await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("offer_yes:"))
async def cb_offer_yes(call: CallbackQuery, state: FSMContext):
    user = await ensure_user(call)
    if not user:
        return
    await state.set_state(Flow.offer_reply)
    await state.update_data(offer_id=int(call.data.split(":")[1]))
    await call.message.answer("Напишіть вилку ціни, строк і зручний час, наприклад:\n300-500 грн, 1 день, завтра о 10:00")
    await call.answer()


@router.message(Flow.offer_reply, F.text)
async def on_offer_reply(message: Message, state: FSMContext):
    user = await ensure_user(message)
    if not user:
        return
    data = await state.get_data()
    ok, text = await run(logic.offer_accept, user, data["offer_id"], message.text)
    if ok:
        await state.clear()
    await message.answer(("✅ " if ok else "⚠️ ") + text)


@router.message(Command("visit"))
async def cmd_visit(message: Message, command: CommandObject):
    user = await ensure_user(message)
    parts = (command.args or "").split()
    if not user:
        return
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: /visit НОМЕР_ЗАЯВКИ КОД")
        return
    ok, text = await run(logic.provider_visit, user, int(parts[0]), parts[1])
    await message.answer(("✅ " if ok else "⚠️ ") + text)


@router.message(Command("estimate"))
async def cmd_estimate(message: Message, command: CommandObject):
    user = await ensure_user(message)
    parts = (command.args or "").split(maxsplit=2)
    if not user:
        return
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("Формат: /estimate НОМЕР СУМА опис робіт")
        return
    ok, text = await run(logic.provider_estimate, user, int(parts[0]), parts[1], parts[2] if len(parts) > 2 else "")
    await message.answer(("✅ " if ok else "⚠️ ") + text)


@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject):
    user = await ensure_user(message)
    parts = (command.args or "").split()
    if not user:
        return
    if not parts or not parts[0].isdigit():
        await message.answer("Формат: /done НОМЕР [СУМА] [ДНІВ_ГАРАНТІЇ]")
        return
    ok, text = await run(logic.provider_done, user, int(parts[0]),
                         parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None)
    await message.answer(("✅ " if ok else "⚠️ ") + text)


# ---------- Гараж ----------

@router.message(Command("garage"))
async def cmd_garage(message: Message):
    user = await ensure_user(message)
    if not user:
        return
    vehicles = await run(logic.vehicles_list, user)
    if not vehicles:
        await message.answer("У гаражі ще немає авто. Додайте: /addcar VIN або /addcar Toyota Camry 2015")
        return
    lines = [f"🚗 {v.display_name}" + (f" · {v.mileage} км" if v.mileage else "") + (f"\n   VIN {v.vin}" if v.vin else "")
             for v in vehicles]
    await message.answer("\n".join(lines) + "\n\n/plan: план ТО · /expenses: витрати")


@router.message(Command("addcar"))
async def cmd_addcar(message: Message, command: CommandObject, state: FSMContext):
    user = await ensure_user(message)
    if not user:
        return
    if not command.args:
        await state.set_state(Flow.add_car)
        await message.answer("Надішліть VIN (17 знаків) або «Марка Модель Рік», наприклад: Toyota Camry 2015")
        return
    await _add_car(message, user, command.args, state)


@router.message(Flow.add_car, F.text)
async def on_add_car(message: Message, state: FSMContext):
    user = await ensure_user(message)
    if user:
        await _add_car(message, user, message.text, state)


async def _add_car(message: Message, user, text: str, state: FSMContext):
    try:
        vehicle = await run(logic.add_vehicle_from_text, user, text)
    except (ValueError, ValidationError) as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(f"✅ Додано: {vehicle.display_name}. Тепер надсилайте фото чеків або дивіться /plan.")


@router.message(Command("plan"))
async def cmd_plan(message: Message):
    user = await ensure_user(message)
    if not user:
        return
    vehicle = await run(logic.current_vehicle, user)
    await message.answer(await run(logic.plan_text, vehicle) if vehicle else "Спершу додайте авто: /addcar")


@router.message(Command("expenses"))
async def cmd_expenses(message: Message):
    user = await ensure_user(message)
    if not user:
        return
    vehicle = await run(logic.current_vehicle, user)
    await message.answer(await run(logic.expenses_text, vehicle) if vehicle else "Спершу додайте авто: /addcar")


@router.message(F.photo)
async def on_photo(message: Message):
    user = await ensure_user(message)
    if not user:
        return
    buf = io.BytesIO()
    await message.bot.download(message.photo[-1], destination=buf)
    await message.answer("Розпізнаю чек…")
    ok, text = await run(logic.receipt_from_photo, user, buf.getvalue())
    await message.answer(("✅ " if ok else "⚠️ ") + text)
