import tempfile
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import linking
from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.testing import make_world
from apps.garage.models import GarageRecord, MaintenanceRule, Vehicle
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.bot import logic, reminders
from apps.bot.runner import deliver_pending, keyboard_for


class OfferParsingTests(TestCase):
    def test_range_with_rest(self):
        lo, hi, rest = logic.parse_offer_text("300-500 грн, 1 день, завтра о 10:00")
        self.assertEqual((lo, hi), (Decimal("300"), Decimal("500")))
        self.assertEqual(rest, "1 день, завтра о 10:00")

    def test_single_number_and_reversed_range(self):
        self.assertEqual(logic.parse_offer_text("від 1 500 грн")[:2], (Decimal("1500"), Decimal("1500")))
        self.assertEqual(logic.parse_offer_text("900–400")[:2], (Decimal("400"), Decimal("900")))

    def test_no_price(self):
        self.assertIsNone(logic.parse_offer_text("зателефонуйте мені")[0])


class ClientFlowThroughBotTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.owner = User.objects.create_user("prov", password="x", role=User.Role.PROVIDER, telegram_id=555)
        self.w["provider"].owner = self.owner
        self.w["provider"].save()
        self.client_user, _ = logic.get_or_create_tg_user(111, "Тарас")
        logic.grant_consent(self.client_user, ai_dialogs=False)

    def test_new_tg_user_needs_consent(self):
        user, created = logic.get_or_create_tg_user(222, "Ніка")
        self.assertTrue(created)
        self.assertFalse(logic.has_consent(user))
        self.assertFalse(user.has_usable_password())
        logic.grant_consent(user, ai_dialogs=True)
        self.assertTrue(logic.has_consent(user))
        self.assertEqual(logic.get_or_create_tg_user(222)[0].pk, user.pk)

    @override_settings(GEMINI_API_KEY="")
    def test_ai_then_order_notifies_provider_with_buttons(self):
        res = logic.run_ai(self.client_user, "стукає підвіска спереду")
        self.assertTrue(res["providers"])
        order = logic.create_order_for(self.client_user, "стукає підвіска спереду",
                                       provider_id=res["providers"][0][0], service_slug=res["service_slug"])
        self.assertEqual(order.source, Order.Source.BOT)
        note = Notification.objects.get(user=self.owner, kind="offer_request")
        self.assertIn("Нова заявка", note.text)
        self.assertNotIn(str(self.client_user.telegram_id), note.text, "контакти клієнта виконавцю не показуємо")
        kb = keyboard_for(note.kind, note.payload)
        self.assertEqual([b.callback_data for b in kb.inline_keyboard[0]],
                         [f"offer_yes:{note.payload['offer_id']}", f"offer_no:{note.payload['offer_id']}"])

    def test_full_deal_over_bot_and_notifications(self):
        order = logic.create_order_for(self.client_user, "Стукає спереду", provider_id=self.w["provider"].pk,
                                       service_slug="diag-chassis")
        offer = order.offers.get()

        # чужий виконавець не може відповісти
        stranger = User.objects.create_user("x", password="x", telegram_id=999)
        self.assertFalse(logic.offer_accept(stranger, offer.pk, "100-200")[0])
        self.assertFalse(logic.offer_accept(self.owner, offer.pk, "без ціни")[0])
        ok, _ = logic.offer_accept(self.owner, offer.pk, "300-500 грн, 1 день, завтра о 10:00")
        self.assertTrue(ok)
        proposed = Notification.objects.get(user=self.client_user, kind="offer_proposed")
        self.assertIn("300–500 грн", proposed.text)
        self.assertEqual(keyboard_for(proposed.kind, proposed.payload).inline_keyboard[0][0].callback_data, f"book:{offer.pk}")

        # клієнт бачить кнопку запису; чужа людина не може записатись
        rows = logic.orders_overview(self.client_user)
        self.assertIn(f"book:{offer.pk}", [d for _, d in rows[0]["buttons"]])
        self.assertFalse(logic.book_offer(stranger, offer.pk)[0])
        self.assertTrue(logic.book_offer(self.client_user, offer.pk)[0])
        order.refresh_from_db()
        booked = Notification.objects.get(user=self.client_user, kind="booked")
        self.assertIn(order.visit_code, booked.text)
        self.assertIn("+380501112233", booked.text)
        self.assertTrue(Notification.objects.filter(user=self.owner, kind="provider_booked").exists())

        # виконавець: візит, кошторис
        self.assertFalse(logic.provider_visit(self.owner, order.pk, "000000" if order.visit_code != "000000" else "111111")[0])
        self.assertTrue(logic.provider_visit(self.owner, order.pk, order.visit_code)[0])
        self.assertTrue(logic.provider_estimate(self.owner, order.pk, "1800", "Заміна сайлентблоків")[0])
        est = Notification.objects.get(user=self.client_user, kind="estimate")
        self.assertEqual(keyboard_for(est.kind, est.payload).inline_keyboard[0][0].callback_data, f"est:{order.pk}")
        self.assertTrue(logic.approve_estimate(self.client_user, order.pk)[0])

        # підтвердження обох сторін
        self.assertTrue(logic.provider_done(self.owner, order.pk, "1750", "30")[0])
        self.assertTrue(Notification.objects.filter(user=self.client_user, kind="confirm_work").exists())
        self.assertTrue(logic.client_confirm(self.client_user, order.pk)[0])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DONE)
        review_note = Notification.objects.get(user=self.client_user, kind="review_request")
        self.assertEqual(len(keyboard_for(review_note.kind, review_note.payload).inline_keyboard[0]), 5)

        self.assertTrue(logic.rate_order(self.client_user, order.pk, 5)[0])
        self.assertFalse(logic.rate_order(self.client_user, order.pk, 5)[0], "повторний відгук неможливий")

    def test_provider_cannot_touch_foreign_order(self):
        order = order_services.create_order(client=self.w["client"], city=self.w["city"], description="x")
        self.assertFalse(logic.provider_visit(self.owner, order.pk, "123456")[0])
        self.assertFalse(logic.provider_done(self.owner, order.pk)[0])

    def test_client_cannot_cancel_foreign_order(self):
        order = order_services.create_order(client=self.w["client"], city=self.w["city"], description="x")
        self.assertFalse(logic.cancel_order(self.client_user, order.pk)[0])

    def test_decline_notifies_liaison(self):
        liaison = User.objects.create_user("liaison", password="x", role=User.Role.LIAISON, telegram_id=777)
        order = logic.create_order_for(self.client_user, "Стукає", provider_id=self.w["provider"].pk)
        self.assertTrue(Notification.objects.filter(user=liaison, kind="new_order").exists())
        self.assertTrue(logic.offer_decline(self.owner, order.offers.get().pk)[0])
        self.assertTrue(Notification.objects.filter(user=liaison, kind="offer_declined").exists())


class GarageViaBotTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.user, _ = logic.get_or_create_tg_user(321, "Іван")

    def test_add_vehicle_by_vin(self):
        v = logic.add_vehicle_from_text(self.user, "wvwzzz1kzaw123456")
        self.assertEqual(str(v.brand), "Volkswagen")
        self.assertEqual(v.year, 2010)
        with self.assertRaises(ValueError):
            logic.add_vehicle_from_text(self.user, "WVWZZZ1KZAW123456")

    def test_add_vehicle_by_text(self):
        v = logic.add_vehicle_from_text(self.user, "Toyota Land Cruiser 2008")
        self.assertEqual((str(v.brand), v.model_name, v.year), ("Toyota", "Land Cruiser", 2008))
        v2 = logic.add_vehicle_from_text(self.user, "Москвич 412")
        self.assertIsNone(v2.brand)
        self.assertEqual(v2.model_name, "Москвич 412")

    @override_settings(GEMINI_API_KEY="", MEDIA_ROOT=tempfile.mkdtemp())
    def test_receipt_requires_vehicle_and_reports_failure(self):
        self.assertFalse(logic.receipt_from_photo(self.user, b"x")[0])
        logic.add_vehicle_from_text(self.user, "Toyota Camry 2015")
        ok, text = logic.receipt_from_photo(self.user, b"not-an-image")
        self.assertFalse(ok)
        self.assertIn("Не вдалося", text)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_receipt_success_message(self):
        logic.add_vehicle_from_text(self.user, "Toyota Camry 2015")
        parsed = {"date": "2026-10-01", "total": 1200, "title": "Заміна мастила", "works": [], "parts": [], "kind": "service"}
        with mock.patch("apps.garage.services.parse_receipt_with_gemini", return_value=parsed):
            ok, text = logic.receipt_from_photo(self.user, b"img")
        self.assertTrue(ok, text)
        self.assertIn("Заміна мастила", text)
        self.assertEqual(GarageRecord.objects.count(), 1)

    def test_plan_and_expenses_text(self):
        v = logic.add_vehicle_from_text(self.user, "Toyota Camry 2015")
        self.assertIn("сервісною книжкою", logic.plan_text(v))
        self.assertIn("Витрат ще немає", logic.expenses_text(v))


class LinkingTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def test_link_moves_data_from_temporary_tg_user(self):
        tg_user, _ = logic.get_or_create_tg_user(4242, "Тимчасовий")
        vehicle = logic.add_vehicle_from_text(tg_user, "Toyota Camry 2015")
        order = logic.create_order_for(tg_user, "Стукає", provider_id=None)
        ok, msg = linking.link_telegram(4242, linking.make_token(self.w["client"]))
        self.assertTrue(ok, msg)
        self.w["client"].refresh_from_db()
        self.assertEqual(self.w["client"].telegram_id, 4242)
        vehicle.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(vehicle.owner, self.w["client"])
        self.assertEqual(order.client, self.w["client"])
        self.assertFalse(User.objects.filter(username="tg_4242").exists())

    def test_bad_and_expired_token(self):
        self.assertFalse(linking.link_telegram(1, "garbage")[0])
        token = linking.make_token(self.w["client"])
        with mock.patch.object(linking, "MAX_AGE_SECONDS", -1):
            self.assertFalse(linking.link_telegram(1, token)[0])

    def test_cannot_hijack_full_account(self):
        other = User.objects.create_user("other", password="pass12345", telegram_id=888)
        ok, msg = linking.link_telegram(888, linking.make_token(self.w["client"]))
        self.assertFalse(ok)
        other.refresh_from_db()
        self.assertEqual(other.telegram_id, 888)

    def test_token_is_short_and_fits_telegram_start_parameter(self):
        token = linking.make_token(self.w["client"])
        self.assertRegex(token, r"^[0-9a-z]+-[0-9a-z]+-[0-9a-f]{20}$")
        self.assertLessEqual(len(linking.START_PREFIX + token), 64, "ліміт Telegram для параметра start")

    def test_token_works_with_or_without_start_prefix_and_rejects_forgeries(self):
        from django.utils.http import int_to_base36
        client = self.w["client"]
        token = linking.make_token(client)
        uid, ts, mac = token.split("-")
        self.assertEqual(linking.read_token(token), client)
        self.assertEqual(linking.read_token(linking.START_PREFIX + token), client)
        self.assertEqual(linking.read_token(f"  {token}\n"), client)
        forged = [
            f"{int_to_base36(client.pk + 1)}-{ts}-{mac}",   # чужий акаунт з чужим підписом
            f"{uid}-{ts}-{'0' * 20}",                        # підпис підібраний
            f"{uid}-{int_to_base36(int(ts, 36) + 999)}-{mac}",  # підмінено час
            f"{uid}-{ts}", f"{token}-x", "--", "", "link-", f"zzzz-{ts}-{mac}", token.upper(),
        ]
        for bad in forged:
            self.assertIsNone(linking.read_token(bad), bad)

    def test_token_of_deactivated_user_is_dead(self):
        token = linking.make_token(self.w["client"])
        User.objects.filter(pk=self.w["client"].pk).update(is_active=False)
        self.assertFalse(linking.link_telegram(5, token)[0])


class BotLinkTests(TestCase):
    """Посилання на бота в профілі: є лише тоді, коли бот справді існує."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = make_world()["client"]

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="")
    def test_no_bot_means_no_links(self):
        self.assertEqual(linking.bot_username(), "")
        self.assertIsNone(linking.bot_links(self.user))

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="@autohub_bot")
    def test_username_from_settings_gives_deep_link_that_really_links(self):
        self.assertEqual(linking.bot_username(), "autohub_bot")
        links = linking.bot_links(self.user)
        self.assertEqual(links["bot_url"], "https://t.me/autohub_bot")
        self.assertFalse(links["linked"])
        prefix = "https://t.me/autohub_bot?start=link-"
        self.assertTrue(links["link_url"].startswith(prefix))
        token = links["link_url"][len(prefix):]
        self.assertEqual(token, links["code"])
        self.assertTrue(linking.link_telegram(4321, token)[0])
        self.user.refresh_from_db()
        self.assertEqual(self.user.telegram_id, 4321)

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="autohub_bot")
    def test_already_linked_user_gets_only_the_bot_link(self):
        self.user.telegram_id = 77
        links = linking.bot_links(self.user)
        self.assertTrue(links["linked"])
        self.assertEqual((links["link_url"], links["code"]), ("", ""))
        self.assertEqual(links["bot_url"], "https://t.me/autohub_bot")

    @override_settings(TELEGRAM_BOT_TOKEN="123:SECRET", TELEGRAM_BOT_USERNAME="")
    def test_username_found_via_getme_is_cached(self):
        with mock.patch.object(linking, "_fetch_bot_username", return_value="found_bot") as fetch:
            self.assertEqual(linking.bot_username(), "found_bot")
            self.assertEqual(linking.bot_username(), "found_bot")
        self.assertEqual(fetch.call_count, 1)

    @override_settings(TELEGRAM_BOT_TOKEN="123:SECRET", TELEGRAM_BOT_USERNAME="")
    def test_getme_failure_hides_links_is_cached_and_never_logs_the_token(self):
        with mock.patch.object(linking, "_fetch_bot_username", side_effect=OSError("https://api.telegram.org/bot123:SECRET/getMe")) as fetch:
            with self.assertLogs("apps.accounts.linking", level="WARNING") as logs:
                self.assertEqual(linking.bot_username(), "")
            self.assertEqual(linking.bot_username(), "", "невдалу спробу не повторюємо при кожному відкритті сторінки")
            self.assertIsNone(linking.bot_links(self.user))
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn("SECRET", "\n".join(logs.output))

    @override_settings(TELEGRAM_BOT_TOKEN="123:SECRET", TELEGRAM_BOT_USERNAME="")
    def test_fetch_reads_username_from_telegram_response(self):
        import io
        import json
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({"ok": True, "result": {"username": "real_bot"}}).encode())
        with mock.patch.object(linking.urllib.request, "urlopen", return_value=response) as opened:
            self.assertEqual(linking._fetch_bot_username(), "real_bot")
        self.assertIn("/getMe", opened.call_args.args[0])


class RemindersTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.w["client"].telegram_id = 100
        self.w["client"].save()
        self.owner = User.objects.create_user("prov", password="x", role=User.Role.PROVIDER, telegram_id=200)
        self.w["provider"].owner = self.owner
        self.w["provider"].save()

    def test_visit_reminder_once(self):
        order = order_services.create_order(client=self.w["client"], city=self.w["city"], description="x",
                                            provider=self.w["provider"])
        offer = order.offers.get()
        order_services.provider_respond(offer, accept=True, price_min=1, price_max=2)
        order_services.book_visit(order, offer, timezone.now() + timedelta(hours=2))
        self.assertEqual(reminders.schedule_visit_reminders(), 2)
        self.assertEqual(reminders.schedule_visit_reminders(), 0, "повторно не нагадуємо")

    def test_visit_reminder_not_for_far_future(self):
        order = order_services.create_order(client=self.w["client"], city=self.w["city"], description="x",
                                            provider=self.w["provider"])
        offer = order.offers.get()
        order_services.provider_respond(offer, accept=True, price_min=1, price_max=2)
        order_services.book_visit(order, offer, timezone.now() + timedelta(days=2))
        self.assertEqual(reminders.schedule_visit_reminders(), 0)

    def test_maintenance_reminder_dedup(self):
        rule = MaintenanceRule.objects.create(title="Мастило", interval_km=10000, interval_months=12)
        GarageRecord.objects.create(vehicle=self.w["vehicle"], title="Мастило", date=timezone.localdate() - timedelta(days=400),
                                    mileage=130000, rule=rule)
        self.assertEqual(reminders.schedule_maintenance_reminders(), 1)
        self.assertEqual(reminders.schedule_maintenance_reminders(), 0)
        self.assertIn("сервісною книжкою", Notification.objects.get(kind="maintenance").text)


class DeliveryTests(TestCase):
    """`async_to_sync` змушує thread-sensitive виклики БД виконуватись у потоці тесту (та сама транзакція)."""

    def setUp(self):
        self.w = make_world()
        self.w["client"].telegram_id = 100
        self.w["client"].save()

    def test_delivery_marks_sent_and_retries_failures(self):
        from apps.core.notifications import notify

        notify(self.w["client"], "Привіт", kind="info")
        bot = SimpleNamespace(send_message=mock.AsyncMock())
        self.assertEqual(async_to_sync(deliver_pending)(bot), 1)
        bot.send_message.assert_awaited_once()
        self.assertEqual(async_to_sync(deliver_pending)(bot), 0, "вже надіслане не повторюється")

        notify(self.w["client"], "Другий", kind="info")
        bot.send_message = mock.AsyncMock(side_effect=RuntimeError("network"))
        self.assertEqual(async_to_sync(deliver_pending)(bot), 0)
        n = Notification.objects.get(text="Другий")
        self.assertEqual((n.attempts, n.sent_at), (1, None))

    def test_users_without_telegram_get_nothing(self):
        from apps.core.notifications import notify

        nobody = User.objects.create_user("nobody", password="x")
        self.assertIsNone(notify(nobody, "x"))
        self.assertEqual(Notification.objects.count(), 0)
