"""Шар aiogram: справжній Dispatcher + Bot із підміненою HTTP-сесією (без мережі й токена).

`apps/bot/tests.py` перевіряє логіку (`logic.py`). Тут перевіряється те, що там не видно: маршрутизація команд і
кнопок, стан діалогу (FSM), згода перед першою дією, тексти й клавіатури, які реально отримує користувач Telegram,
та доставка сповіщень із черги.
"""
import tempfile
from datetime import datetime
from unittest import mock

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendChatAction, SendMessage
from aiogram.types import Chat, Message
from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.assistant import services as ai
from apps.bot import handlers, logic
from apps.bot.runner import deliver_pending
from apps.core.models import Notification
from apps.core.testing import make_world
from apps.garage.models import GarageRecord, Vehicle
from apps.orders.models import Order
from apps.reviews.models import Review

CLIENT_TG = 111
PROVIDER_TG = 555


class FakeSession(BaseSession):
    """Запам'ятовує, що бот намагався надіслати, і відповідає як Telegram."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=1000 + len(self.calls), date=datetime.now(),
                           chat=Chat(id=int(method.chat_id), type="private"), text=method.text)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b""


def _buttons(markup) -> list[str]:
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@override_settings(GEMINI_API_KEY="", MEDIA_ROOT=tempfile.mkdtemp())
class BotHandlersTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.owner = User.objects.create_user("prov", password="x", role=User.Role.PROVIDER, telegram_id=PROVIDER_TG)
        self.w["provider"].owner = self.owner
        self.w["provider"].save()
        self.session = FakeSession()
        self.bot = Bot(token="123456:TEST-TOKEN", session=self.session)
        self.dp = Dispatcher(storage=MemoryStorage())
        # module-level router can belong to one parent only; detach it from a previous test's dispatcher
        handlers.router._parent_router = None
        self.dp.include_router(handlers.router)
        self._update_id = 0
        self._message_id = 0

    # ---------- допоміжне ----------

    def _next_ids(self):
        self._update_id += 1
        self._message_id += 1
        return self._update_id, self._message_id

    def _sender(self, tg_id):
        return {"id": tg_id, "is_bot": False, "first_name": "Тарас" if tg_id == CLIENT_TG else "Майстер"}

    def say(self, tg_id, text):
        """Користувач пише боту (текст або команду)."""
        uid, mid = self._next_ids()
        message = {"message_id": mid, "date": int(datetime.now().timestamp()), "chat": {"id": tg_id, "type": "private"},
                   "from": self._sender(tg_id), "text": text}
        if text.startswith("/"):
            command = text.split()[0]
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
        async_to_sync(self.dp.feed_raw_update)(self.bot, {"update_id": uid, "message": message})

    def press(self, tg_id, data):
        """Користувач натискає inline-кнопку."""
        uid, mid = self._next_ids()
        message = {"message_id": mid, "date": int(datetime.now().timestamp()), "chat": {"id": tg_id, "type": "private"},
                   "from": {"id": 1, "is_bot": True, "first_name": "bot"}, "text": "кнопка"}
        callback = {"id": str(uid), "from": self._sender(tg_id), "chat_instance": "ci", "data": data, "message": message}
        async_to_sync(self.dp.feed_raw_update)(self.bot, {"update_id": uid, "callback_query": callback})

    def send_photo(self, tg_id):
        uid, mid = self._next_ids()
        message = {"message_id": mid, "date": int(datetime.now().timestamp()), "chat": {"id": tg_id, "type": "private"},
                   "from": self._sender(tg_id),
                   "photo": [{"file_id": "small", "file_unique_id": "s", "width": 10, "height": 10},
                             {"file_id": "big", "file_unique_id": "b", "width": 800, "height": 600}]}

        async def fake_download(bot_self, file, destination=None, **kwargs):
            destination.write(b"\xff\xd8fake-jpeg")

        with mock.patch.object(Bot, "download", fake_download):
            async_to_sync(self.dp.feed_raw_update)(self.bot, {"update_id": uid, "message": message})

    def sent(self, tg_id=None):
        """Усі надіслані повідомлення (SendMessage), за потреби лише для одного чату."""
        return [c for c in self.session.calls if isinstance(c, SendMessage) and (tg_id is None or int(c.chat_id) == tg_id)]

    def last_text(self, tg_id=None):
        return self.sent(tg_id)[-1].text

    def last_buttons(self, tg_id=None):
        return _buttons(self.sent(tg_id)[-1].reply_markup)

    def alerts(self):
        return [c for c in self.session.calls if isinstance(c, AnswerCallbackQuery)]

    def deliver(self):
        """Прогнати чергу сповіщень через бота (як це робить runbot)."""
        return async_to_sync(deliver_pending)(self.bot)

    def consent(self, tg_id, *, ai_dialogs=False):
        self.say(tg_id, "/start")
        self.press(tg_id, "consent:ai" if ai_dialogs else "consent:noai")

    def start_deal(self):
        """Клієнт зі згодою пише про проблему й створює заявку до виконавця; виконавець теж дав згоду."""
        self.consent(CLIENT_TG)
        self.consent(PROVIDER_TG)
        self.say(CLIENT_TG, "стукає підвіска спереду")
        self.press(CLIENT_TG, f"order:{self.w['provider'].pk}")
        return Order.objects.get()

    # ---------- згода, довідка ----------

    def test_start_asks_for_consent_first(self):
        self.say(CLIENT_TG, "/start")
        self.assertIn("згода на обробку персональних даних", self.last_text())
        self.assertEqual(self.last_buttons(), ["consent:ai", "consent:noai"])
        user = User.objects.get(telegram_id=CLIENT_TG)
        self.assertIsNone(user.personal_data_consent_at)

    def test_consent_buttons_store_choice_and_show_help(self):
        self.say(CLIENT_TG, "/start")
        self.press(CLIENT_TG, "consent:ai")
        user = User.objects.get(telegram_id=CLIENT_TG)
        self.assertIsNotNone(user.personal_data_consent_at)
        self.assertTrue(user.ai_dialog_consent)
        self.assertIn("/orders", self.last_text())
        self.assertTrue(any(isinstance(c, EditMessageReplyMarkup) for c in self.session.calls), "кнопки прибрано")

        self.say(PROVIDER_TG, "/start")
        self.press(PROVIDER_TG, "consent:noai")
        self.assertFalse(User.objects.get(telegram_id=PROVIDER_TG).ai_dialog_consent)

    def test_start_after_consent_greets(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "/start")
        self.assertTrue(self.last_text().startswith("Вітаю!"))

    def test_help_privacy_and_myid_work_without_consent(self):
        self.say(CLIENT_TG, "/help")
        self.assertIn("/addcar", self.last_text())
        self.say(CLIENT_TG, "/privacy")
        self.assertIn("мінімум даних", self.last_text())
        self.say(CLIENT_TG, "/myid")
        self.assertIn(str(CLIENT_TG), self.last_text())

    def test_nothing_works_before_consent(self):
        for text in ("стукає підвіска спереду", "/orders", "/garage", "/plan", "/expenses", "/addcar", "/done 1"):
            self.say(CLIENT_TG, text)
            self.assertIn("згода на обробку персональних даних", self.last_text(), text)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Vehicle.objects.filter(owner__telegram_id=CLIENT_TG).count(), 0)

    # ---------- клієнт: ШІ та заявка ----------

    def test_ai_answer_has_provider_buttons_and_order_button(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "стукає підвіска спереду")
        self.assertTrue(any(isinstance(c, SendChatAction) for c in self.session.calls), "індикатор «друкує»")
        self.assertIn("СТО Тест", self.last_text())
        self.assertIn("припущення", self.last_text().lower())
        self.assertEqual(self.last_buttons(), [f"order:{self.w['provider'].pk}", "order:0"])

    def test_too_short_text_asks_for_details(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "ну")
        self.assertIn("докладніше", self.last_text())
        self.assertEqual(self.last_buttons(), [])

    @override_settings(GEMINI_API_KEY="test-key")
    def test_off_topic_text_gets_no_order_buttons(self):
        fake = {"possible_causes": [], "what_to_do": "Опишіть симптоми несправності.", "service_type_slug": None,
                "clarifying_questions": ["Що саме не так?"], "is_safety_related": False}
        self.consent(CLIENT_TG)
        with mock.patch.object(ai, "_call_llm", return_value=fake):
            self.say(CLIENT_TG, "Яка столиця Франції?")
        self.assertIn("Опишіть симптоми", self.last_text())
        self.assertEqual(self.last_buttons(), [], "для чужої теми заявку не пропонуємо")
        self.press(CLIENT_TG, "order:0")
        self.assertEqual(Order.objects.count(), 0)

    def test_order_button_without_question_is_rejected(self):
        self.consent(CLIENT_TG)
        self.press(CLIENT_TG, "order:0")
        self.assertTrue(self.alerts()[-1].show_alert)
        self.assertIn("Спершу опишіть", self.alerts()[-1].text)
        self.assertEqual(Order.objects.count(), 0)

    def test_order_creation_reaches_provider_with_buttons(self):
        order = self.start_deal()
        self.assertEqual(order.source, Order.Source.BOT)
        self.assertEqual(order.description, "стукає підвіска спереду")
        self.assertIn(f"Заявку №{order.pk} створено", self.last_text(CLIENT_TG))

        self.assertGreaterEqual(self.deliver(), 1, "виконавець отримав сповіщення про заявку")
        self.assertIn("Нова заявка", self.last_text(PROVIDER_TG))
        offer = order.offers.get()
        self.assertEqual(self.last_buttons(PROVIDER_TG), [f"offer_yes:{offer.pk}", f"offer_no:{offer.pk}"])
        self.assertNotIn(str(CLIENT_TG), self.last_text(PROVIDER_TG), "контакти клієнта виконавцю не показуємо")

    def test_order_without_choice_goes_to_liaison(self):
        liaison = User.objects.create_user("liaison", password="x", role=User.Role.LIAISON, telegram_id=777)
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "стукає підвіска спереду")
        self.press(CLIENT_TG, "order:0")
        order = Order.objects.get()
        self.assertIsNone(order.provider)
        self.assertTrue(Notification.objects.filter(user=liaison, kind="new_order").exists())

    def test_orders_command_lists_and_empty(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "/orders")
        self.assertIn("Заявок поки немає", self.last_text())
        self.say(CLIENT_TG, "стукає підвіска спереду")
        self.press(CLIENT_TG, "order:0")
        self.say(CLIENT_TG, "/orders")
        self.assertIn("№", self.last_text())
        self.assertIn(f"cancel:{Order.objects.get().pk}", self.last_buttons())

    def test_client_can_cancel_through_button(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "стукає підвіска спереду")
        self.press(CLIENT_TG, "order:0")
        order = Order.objects.get()
        self.press(CLIENT_TG, f"cancel:{order.pk}")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_foreign_button_is_refused_with_alert(self):
        order = self.start_deal()
        self.consent(999)
        self.press(999, f"cancel:{order.pk}")
        self.assertTrue(self.alerts()[-1].show_alert, "чужу заявку скасувати не можна")
        order.refresh_from_db()
        self.assertNotEqual(order.status, Order.Status.CANCELLED)

    # ---------- повна угода через обох користувачів ----------

    def test_full_deal_through_telegram_handlers(self):
        order = self.start_deal()
        offer = order.offers.get()
        self.deliver()

        # виконавець: кнопка «Беру» → FSM очікує вилку → відповідь
        self.press(PROVIDER_TG, f"offer_yes:{offer.pk}")
        self.assertIn("Напишіть вилку ціни", self.last_text(PROVIDER_TG))
        self.say(PROVIDER_TG, "без ціни")
        self.assertIn("⚠️", self.last_text(PROVIDER_TG), "некоректна відповідь не закриває стан")
        self.say(PROVIDER_TG, "300-500 грн, 1 день, завтра о 10:00")
        self.assertIn("✅", self.last_text(PROVIDER_TG))

        # клієнт отримує пропозицію з кнопкою запису
        self.deliver()
        self.assertIn("300–500 грн", self.last_text(CLIENT_TG))
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"book:{offer.pk}"])
        self.press(CLIENT_TG, f"book:{offer.pk}")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.BOOKED)
        self.deliver()
        self.assertIn(order.visit_code, self.last_text(CLIENT_TG))
        self.assertIn("+380501112233", self.last_text(CLIENT_TG), "контакти після підтвердженого запису")

        # виконавець: візит за кодом клієнта, кошторис
        self.say(PROVIDER_TG, f"/visit {order.pk}")
        self.assertIn("Формат: /visit", self.last_text(PROVIDER_TG))
        self.say(PROVIDER_TG, f"/visit {order.pk} 000x00")
        self.assertIn("⚠️", self.last_text(PROVIDER_TG))
        self.say(PROVIDER_TG, f"/visit {order.pk} {order.visit_code}")
        self.assertIn("✅", self.last_text(PROVIDER_TG))
        self.say(PROVIDER_TG, "/estimate")
        self.assertIn("Формат: /estimate", self.last_text(PROVIDER_TG))
        self.say(PROVIDER_TG, f"/estimate {order.pk} 1800 Заміна сайлентблоків")
        self.assertIn("✅", self.last_text(PROVIDER_TG))
        self.deliver()
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"est:{order.pk}"])
        self.press(CLIENT_TG, f"est:{order.pk}")

        # виконавець завершує, клієнт підтверджує, лишає відгук
        self.say(PROVIDER_TG, "/done")
        self.assertIn("Формат: /done", self.last_text(PROVIDER_TG))
        self.say(PROVIDER_TG, f"/done {order.pk} 1750 30")
        self.assertIn("✅", self.last_text(PROVIDER_TG))
        self.deliver()
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"cfm:{order.pk}"])
        self.press(CLIENT_TG, f"cfm:{order.pk}")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DONE)
        self.deliver()
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"rate:{order.pk}:{n}" for n in range(1, 6)])
        self.press(CLIENT_TG, f"rate:{order.pk}:5")
        self.assertEqual(Review.objects.get(order=order).rating, 5)
        self.press(CLIENT_TG, f"rate:{order.pk}:4")
        self.assertTrue(self.alerts()[-1].show_alert, "повторний відгук неможливий")

    def test_orders_command_follows_deal_stages(self):
        order = self.start_deal()
        offer = order.offers.get()
        self.deliver()
        self.press(PROVIDER_TG, f"offer_yes:{offer.pk}")
        self.say(PROVIDER_TG, "300-500 грн, 1 день")
        self.deliver()

        self.say(CLIENT_TG, "/orders")
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"book:{offer.pk}", f"cancel:{order.pk}"])
        self.assertNotIn("Код візиту", self.last_text(CLIENT_TG), "до запису контактів і коду немає")

        self.press(CLIENT_TG, f"book:{offer.pk}")
        order.refresh_from_db()
        self.say(CLIENT_TG, "/orders")
        self.assertIn(f"Код візиту: {order.visit_code}", self.last_text(CLIENT_TG))
        self.assertIn("+380501112233", self.last_text(CLIENT_TG))
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"cancel:{order.pk}"])

        self.say(PROVIDER_TG, f"/visit {order.pk} {order.visit_code}")
        self.say(PROVIDER_TG, f"/estimate {order.pk} 1800 Заміна сайлентблоків")
        self.say(CLIENT_TG, "/orders")
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"est:{order.pk}", f"cancel:{order.pk}"])

        self.press(CLIENT_TG, f"est:{order.pk}")
        self.say(CLIENT_TG, "/orders")
        self.assertEqual(self.last_buttons(CLIENT_TG), [f"cfm:{order.pk}", f"cancel:{order.pk}"])

        self.say(PROVIDER_TG, f"/done {order.pk} 1750")
        self.press(CLIENT_TG, f"cfm:{order.pk}")
        self.say(CLIENT_TG, "/orders")
        self.assertEqual(self.last_buttons(CLIENT_TG), [], "завершену заявку скасувати вже не можна")

    def test_client_buttons_for_unknown_order_show_alert(self):
        self.consent(CLIENT_TG)
        for data in ("book:9999", "est:9999", "cfm:9999", "cancel:9999", "rate:9999:5"):
            before = len(self.alerts())
            self.press(CLIENT_TG, data)
            self.assertEqual(len(self.alerts()), before + 1, data)
            self.assertTrue(self.alerts()[-1].show_alert, data)

    def test_buttons_require_consent(self):
        for data in ("order:0", "book:1", "est:1", "cfm:1", "cancel:1", "rate:1:5", "offer_yes:1", "offer_no:1"):
            self.press(CLIENT_TG, data)
            self.assertIn("згода на обробку персональних даних", self.last_text(), data)
        self.assertEqual(Order.objects.count(), 0)

    def test_provider_commands_and_photo_require_consent(self):
        for text in ("/visit 1 123456", "/estimate 1 100", "/plan", "/expenses", "/garage"):
            self.say(CLIENT_TG, text)
            self.assertIn("згода на обробку персональних даних", self.last_text(), text)
        self.send_photo(CLIENT_TG)
        self.assertIn("згода на обробку персональних даних", self.last_text())
        self.assertEqual(GarageRecord.objects.count(), 0)

    def test_estimate_rejects_bad_amount(self):
        order = self.start_deal()
        for args in ("abc", "0", "-5"):
            self.say(PROVIDER_TG, f"/estimate {order.pk} {args} опис")
            self.assertIn("⚠️", self.last_text(PROVIDER_TG), args)

    def test_offer_decline_twice_and_unknown(self):
        order = self.start_deal()
        offer = order.offers.get()
        self.press(PROVIDER_TG, f"offer_no:{offer.pk}")
        self.press(PROVIDER_TG, f"offer_no:{offer.pk}")
        self.assertTrue(self.alerts()[-1].show_alert, "повторна відмова не проходить")
        self.press(PROVIDER_TG, "offer_no:9999")
        self.assertTrue(self.alerts()[-1].show_alert)

    def test_expenses_summary_after_receipt(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "/addcar Toyota Camry 2015")
        self.say(CLIENT_TG, "/expenses")
        self.assertIn("Витрат ще немає", self.last_text())
        parsed = {"date": "2026-10-15", "mileage": 121340, "total": 2320, "title": "Заміна масла", "kind": "service"}
        with mock.patch("apps.garage.services.parse_receipt_with_gemini", return_value=parsed):
            self.send_photo(CLIENT_TG)
        self.say(CLIENT_TG, "/expenses")
        self.assertIn("Витрати:", self.last_text())
        self.assertIn("2320", self.last_text())

    def test_provider_decline_button_notifies_liaison(self):
        liaison = User.objects.create_user("liaison", password="x", role=User.Role.LIAISON, telegram_id=777)
        order = self.start_deal()
        offer = order.offers.get()
        self.press(PROVIDER_TG, f"offer_no:{offer.pk}")
        self.assertTrue(Notification.objects.filter(user=liaison, kind="offer_declined").exists())

    def test_stranger_cannot_use_provider_commands(self):
        order = self.start_deal()
        self.consent(999)
        self.say(999, f"/done {order.pk}")
        self.assertIn("⚠️", self.last_text(999))
        self.say(999, f"/visit {order.pk} {order.visit_code or '123456'}")
        self.assertIn("⚠️", self.last_text(999))

    # ---------- гараж ----------

    def test_garage_empty_then_add_car_by_text(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "/garage")
        self.assertIn("У гаражі ще немає авто", self.last_text())
        self.say(CLIENT_TG, "/plan")
        self.assertIn("Спершу додайте авто", self.last_text())
        self.say(CLIENT_TG, "/expenses")
        self.assertIn("Спершу додайте авто", self.last_text())

        self.say(CLIENT_TG, "/addcar Toyota Camry 2015")
        self.assertIn("✅ Додано", self.last_text())
        self.assertEqual(Vehicle.objects.filter(owner__telegram_id=CLIENT_TG).count(), 1)
        self.say(CLIENT_TG, "/garage")
        self.assertIn("Toyota Camry", self.last_text())
        self.say(CLIENT_TG, "/plan")
        self.assertTrue(self.last_text())
        self.say(CLIENT_TG, "/expenses")
        self.assertTrue(self.last_text())

    def test_addcar_dialog_state_then_vin(self):
        self.consent(CLIENT_TG)
        vin = "WVWZZZ1KZAW123456"
        self.say(CLIENT_TG, "/addcar")
        self.assertIn("Надішліть VIN", self.last_text())
        # поки очікується відповідь, текст іде як дані авто, а не як запит до ШІ
        self.say(CLIENT_TG, vin)
        self.assertIn("✅ Додано", self.last_text())
        self.assertEqual(Vehicle.objects.get(owner__telegram_id=CLIENT_TG).vin, vin)

        # дубль VIN: помилка, а стан діалогу лишається, щоб можна було виправити введене
        self.say(CLIENT_TG, f"/addcar {vin}")
        self.assertIn("⚠️", self.last_text())
        self.say(CLIENT_TG, "/addcar")
        self.say(CLIENT_TG, vin)
        self.assertIn("⚠️", self.last_text())
        self.say(CLIENT_TG, "Toyota Camry 2015")
        self.assertIn("✅ Додано", self.last_text())
        self.assertEqual(Vehicle.objects.filter(owner__telegram_id=CLIENT_TG).count(), 2)

        # після успіху стан закрито: текст знову іде до ШІ
        self.say(CLIENT_TG, "стукає підвіска спереду")
        self.assertIn("припущення", self.last_text().lower())

    def test_receipt_photo_creates_garage_record(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "/addcar Toyota Camry 2015")
        parsed = {"date": "2026-10-15", "mileage": 121340, "total": 2320, "title": "СТО «АвтоМайстер»",
                  "works": ["Заміна масла двигуна"], "parts": ["Масло 5W-30, 4 л"], "kind": "service"}
        with mock.patch("apps.garage.services.parse_receipt_with_gemini", return_value=parsed):
            self.send_photo(CLIENT_TG)
        self.assertIn("Розпізнаю чек", [m.text for m in self.sent(CLIENT_TG)][-2])
        self.assertIn("✅", self.last_text())
        record = GarageRecord.objects.get()
        self.assertEqual(int(record.amount), 2320)

    def test_receipt_photo_without_car_explains_what_to_do(self):
        self.consent(CLIENT_TG)
        self.send_photo(CLIENT_TG)
        self.assertIn("⚠️", self.last_text())
        self.assertEqual(GarageRecord.objects.count(), 0)

    # ---------- /link ----------

    def test_link_command_usage_and_bad_token(self):
        self.consent(CLIENT_TG)
        self.say(CLIENT_TG, "/link")
        self.assertIn("/link КОД", self.last_text())
        self.say(CLIENT_TG, "/link not-a-real-token")
        self.assertIn("⚠️", self.last_text())

    def test_start_with_link_payload_from_site_button_connects_the_account(self):
        from apps.accounts import linking
        token = linking.make_token(self.w["client"])
        self.say(CLIENT_TG, f"/start {linking.START_PREFIX}{token}")
        self.assertTrue(self.last_text().startswith("✅"))
        self.assertIn("/orders", self.last_text(), "після підключення бот показує довідку")
        self.w["client"].refresh_from_db()
        self.assertEqual(self.w["client"].telegram_id, CLIENT_TG)
        self.assertFalse(User.objects.filter(username=f"tg_{CLIENT_TG}").exists())

    def test_start_with_bad_link_payload_warns_and_creates_nobody(self):
        self.say(CLIENT_TG, "/start link-abc-def-123")
        self.assertTrue(self.last_text().startswith("⚠️"))
        self.assertFalse(User.objects.filter(telegram_id=CLIENT_TG).exists())

    def test_link_command_still_accepts_the_new_short_code(self):
        from apps.accounts import linking
        self.say(CLIENT_TG, f"/link {linking.make_token(self.w['client'])}")
        self.assertTrue(self.last_text().startswith("✅"))

    # ---------- доставка сповіщень ----------

    def test_delivery_marks_blocked_bot_as_permanent_failure(self):
        order = self.start_deal()
        note = Notification.objects.filter(user=self.owner, kind="offer_request").get()

        async def forbidden(*args, **kwargs):
            raise TelegramForbiddenError(method=mock.Mock(), message="bot was blocked by the user")

        with mock.patch.object(self.bot, "send_message", forbidden):
            self.assertEqual(self.deliver(), 0)
        note.refresh_from_db()
        self.assertIsNone(note.sent_at)
        self.assertTrue(note.error)
        self.assertEqual(note.attempts, 5, "бот заблокований користувачем: повторів більше немає")
        self.assertEqual(logic.pending_notifications(), [])
        self.assertEqual(order.offers.count(), 1)
