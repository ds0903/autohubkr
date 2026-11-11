"""Гілки веб-інтерфейсу, яких не торкаються основні сценарії: помилкові дії, межі форм, налаштування приватності."""
import io
import sys
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User
from apps.assistant import services as ai
from apps.catalog.models import CarBrand, Provider
from apps.core.testing import make_world
from apps.garage.models import GarageRecord, Receipt, Vehicle
from apps.orders import services as order_services
from apps.orders.models import Order, OrderPhoto
from apps.reviews.models import Review


def png(name="x.png", size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(GEMINI_API_KEY="", MEDIA_ROOT=tempfile.mkdtemp())
class WebEdgeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.w = make_world()
        self.client.login(username="client1", password="pass12345")

    def new_order(self, **kwargs):
        return order_services.create_order(client=self.w["client"], city=self.w["city"], description="Стукає спереду",
                                           provider=self.w["provider"], **kwargs)

    # ---------- приватність і акаунт ----------

    def test_profile_toggles_ai_dialog_consent(self):
        user = self.w["client"]
        self.assertFalse(user.ai_dialog_consent)
        resp = self.client.post(reverse("web:profile"), {"ai_consent": "on"})
        self.assertRedirects(resp, reverse("web:profile"))
        user.refresh_from_db()
        self.assertTrue(user.ai_dialog_consent)
        self.client.post(reverse("web:profile"))
        user.refresh_from_db()
        self.assertFalse(user.ai_dialog_consent, "зняття галочки вимикає збереження діалогів")

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="")
    def test_profile_hides_telegram_block_while_there_is_no_bot(self):
        page = self.client.get(reverse("web:profile")).content.decode()
        self.assertNotIn("Telegram", page)
        self.assertNotIn("/link", page)
        self.assertNotIn("t.me", page)

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="autohub_test_bot")
    def test_profile_offers_one_click_telegram_link_and_then_just_the_bot(self):
        import re
        from apps.accounts import linking
        page = self.client.get(reverse("web:profile")).content.decode()
        self.assertIn('href="https://t.me/autohub_test_bot?start=link-', page)
        self.assertIn("Підключити в Telegram", page)
        self.assertIn("/link ", page, "запасний варіант командою лишається в «Не відкривається?»")
        token = re.search(r"start=link-([0-9a-z-]+)", page).group(1)
        self.assertTrue(linking.link_telegram(4242, token)[0])
        after = self.client.get(reverse("web:profile")).content.decode()
        self.assertIn("Telegram підключено", after)
        self.assertIn('href="https://t.me/autohub_test_bot"', after)
        self.assertNotIn("start=link-", after, "підключеному користувачу новий код не потрібен")

    def test_registration_ticks_dialog_saving_by_default_but_not_data_consent(self):
        import re
        page = self.client_class().get(reverse("web:register")).content.decode()
        ai_box = re.search(r'<input[^>]*name="ai_consent"[^>]*>', page).group(0)
        data_box = re.search(r'<input[^>]*name="consent"[^>]*>', page).group(0)
        self.assertIn("checked", ai_box)
        self.assertNotIn("checked", data_box, "згоду на обробку даних людина дає сама")

    def test_unticked_dialog_saving_stays_unticked_after_a_form_error(self):
        import re
        resp = self.client_class().post(reverse("web:register"), {"username": "tester", "first_name": "Т"})
        ai_box = re.search(r'<input[^>]*name="ai_consent"[^>]*>', resp.content.decode()).group(0)
        self.assertNotIn("checked", ai_box)

    def test_logged_in_user_is_sent_away_from_register(self):
        self.assertRedirects(self.client.get(reverse("web:register")), reverse("web:home"))

    # ---------- ШІ ----------

    @override_settings(GEMINI_API_KEY="test-key")
    def test_ai_uses_selected_vehicle_but_not_a_foreign_one(self):
        stranger = User.objects.create_user("stranger", password="x")
        foreign = Vehicle.objects.create(owner=stranger, model_name="Чужа таратайка", year=2001)
        fake = {"possible_causes": ["x"], "service_type_slug": "diag-chassis"}
        with mock.patch.object(ai, "_call_llm", return_value=fake) as call:
            self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "vehicle": self.w["vehicle"].pk})
            self.assertIn("Camry", call.call_args.args[0])
            self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "vehicle": foreign.pk})
            self.assertNotIn("Чужа таратайка", call.call_args.args[0], "авто іншого власника в запит не потрапляє")

    # ---------- авто вписують самі ----------

    @override_settings(GEMINI_API_KEY="test-key")
    def test_ai_takes_the_car_as_typed_text_without_saving_it(self):
        fake = {"possible_causes": ["x"], "service_type_slug": "diag-chassis"}
        for client in (self.client, self.client_class()):  # залогінений і гість
            with mock.patch.object(ai, "_call_llm", return_value=fake) as call:
                resp = client.post(reverse("web:ask"), {"text": "стукає підвіска", "car": "toyota   Corolla 2012"})
            self.assertEqual(resp.status_code, 200)
            prompt = call.call_args.args[0]
            self.assertIn("Автомобіль: Toyota, Corolla, 2012", prompt, "марка нормалізується за довідником")
        self.assertEqual(Vehicle.objects.count(), 1, "вписане авто не потрапляє в гараж")

    @override_settings(GEMINI_API_KEY="test-key")
    def test_typed_car_equal_to_a_garage_car_brings_its_full_data(self):
        fake = {"possible_causes": ["x"], "service_type_slug": "diag-chassis"}
        with mock.patch.object(ai, "_call_llm", return_value=fake) as call:
            self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "car": "Toyota Camry 2015"})
        self.assertIn("пробіг 150000 км", call.call_args.args[0])

    @override_settings(GEMINI_API_KEY="test-key")
    def test_unknown_brand_and_garbage_in_car_field_do_not_break_the_answer(self):
        fake = {"possible_causes": ["x"], "service_type_slug": "diag-chassis"}
        for car in ("Geely Coolray 2021", "   ", "x" * 500, "<script>alert(1)</script>"):
            with mock.patch.object(ai, "_call_llm", return_value=fake):
                resp = self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "car": car})
            self.assertEqual(resp.status_code, 200, car)
            self.assertNotContains(resp, "<script>alert")

    def test_vehicle_pk_that_is_not_a_number_is_ignored(self):
        resp = self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "vehicle": "abc"})
        self.assertEqual(resp.status_code, 200)

    def test_typed_car_is_not_linked_to_conversation(self):
        from apps.assistant.models import Conversation
        self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "car": "Geely Coolray 2021"})
        self.assertIsNone(Conversation.objects.get().vehicle)
        self.client.post(reverse("web:ask"), {"text": "стукає підвіска", "car": "Toyota Camry 2015"})
        self.assertEqual(Conversation.objects.latest("id").vehicle, self.w["vehicle"])

    def test_home_form_is_a_free_text_field_prefilled_from_garage(self):
        page = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('name="car"', page)
        self.assertIn('value="Toyota Camry 2015"', page)
        self.assertIn('<datalist id="ai-cars">', page)
        self.assertNotIn('<select name="vehicle"', page, "авто не вибирають зі списку")

    def test_home_form_for_guest_has_empty_car_field(self):
        page = self.client_class().get(reverse("web:home")).content.decode()
        self.assertIn('name="car"', page)
        self.assertNotIn("ai-cars", page)

    def test_garage_brand_is_typed_not_selected(self):
        url = reverse("web:garage")
        self.client.post(url, {"brand_name": "toyota", "model_name": "Corolla", "year": 2012})
        known = Vehicle.objects.get(model_name="Corolla")
        self.assertEqual(known.brand, self.w["toyota"])
        before = CarBrand.objects.count()
        self.client.post(url, {"brand_name": "Geely", "model_name": "Coolray", "year": 2021})
        unknown = Vehicle.objects.get(year=2021)
        self.assertIsNone(unknown.brand)
        self.assertEqual(unknown.model_name, "Geely Coolray")
        self.assertEqual(unknown.display_name, "Geely Coolray 2021")
        self.assertEqual(CarBrand.objects.count(), before, "довідник марок не засмічується")

    def test_garage_form_suggests_brands(self):
        page = self.client.get(reverse("web:garage")).content.decode()
        self.assertIn('id="brand-list"', page)
        self.assertIn('<option value="Toyota">', page)
        self.assertIn('list="brand-list"', page)

    # ---------- плавність ----------

    def test_pages_load_motion_script_and_lists_are_marked_for_reveal(self):
        home = self.client.get(reverse("web:home")).content.decode()
        # У бойовому режимі ManifestStaticFilesStorage додає до імені хеш: web/motion.<хеш>.js.
        self.assertRegex(home, r"web/motion(\.[0-9a-f]+)?\.js")
        self.assertIn('<li data-reveal>', home)
        self.assertIn('class="provider" data-reveal', home)
        self.assertIn('document.documentElement.classList.add("js")', home, "клас .js ставиться до першого малювання")
        catalog = self.client.get(reverse("web:catalog")).content.decode()
        self.assertIn('class="provider" data-reveal', catalog)

    def test_motion_assets_exist_and_respect_reduced_motion(self):
        from django.contrib.staticfiles import finders
        js, css = finders.find("web/motion.js"), finders.find("web/style.css")
        self.assertTrue(js and css)
        with open(css, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("@view-transition", text)
        self.assertIn("prefers-reduced-motion: reduce", text)
        reduced = text[text.index("prefers-reduced-motion: reduce"):]
        self.assertIn(".js [data-reveal] { opacity: 1; transform: none; }", reduced)
        with open(js, encoding="utf-8") as f:
            self.assertIn("prefers-reduced-motion", f.read())

    # ---------- каталог і QR ----------

    def test_catalog_brand_filter(self):
        toyota = self.w["toyota"]
        other = CarBrand.objects.create(name="Opel", slug="opel")
        narrow = Provider.objects.create(city=self.w["city"], name="Тільки Опель")
        narrow.categories.add(self.w["cat"])
        narrow.brands.add(other)
        resp = self.client.get(reverse("web:catalog"), {"brand": toyota.slug})
        self.assertContains(resp, "СТО Тест")
        self.assertNotContains(resp, "Тільки Опель")

    def test_qr_unavailable_without_segno_library(self):
        code = self.w["provider"].referral_code
        with mock.patch.dict(sys.modules, {"segno": None}):
            resp = self.client.get(reverse("web:referral_qr", args=[code]))
        self.assertEqual(resp.status_code, 404)

    # ---------- заявка ----------

    def test_order_form_prefilled_from_ai_link_and_lists_orders(self):
        resp = self.client.get(reverse("web:order_new"), {"service": "diag-chassis", "text": "Стукає спереду"})
        form = resp.context["form"]
        self.assertEqual(form.initial["service_type"], self.w["chassis"])
        self.assertEqual(form.initial["description"], "Стукає спереду")
        order = self.new_order()
        self.assertContains(self.client.get(reverse("web:orders")), f"№{order.pk}")

    def test_order_with_photos_and_photo_limit(self):
        url = reverse("web:order_new")
        base = {"description": "Стукає спереду на ямах", "vehicle": self.w["vehicle"].pk}
        resp = self.client.post(url, {**base, "photos": [png("a.png"), png("b.png")]})
        order = Order.objects.get()
        self.assertRedirects(resp, reverse("web:order_detail", args=[order.pk]))
        self.assertEqual(OrderPhoto.objects.filter(order=order).count(), 2)

        resp = self.client.post(url, {**base, "photos": [png(f"{i}.png") for i in range(5)]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "не більше 4 фото")
        self.assertEqual(Order.objects.count(), 1, "заявка з 5 фото не створюється")

    def test_order_action_errors_are_reported_not_raised(self):
        order = self.new_order()
        url = reverse("web:order_action", args=[order.pk])
        for data, expected in (
            ({"action": "nonsense"}, "Невідома дія"),
            ({"action": "book", "offer": "abc"}, "Невірні дані запису"),
            ({"action": "review", "rating": "9"}, "від 1 до 5"),
            ({"action": "approve_estimate"}, None),   # заявка ще не на тому етапі
            ({"action": "confirm_work"}, None),
        ):
            resp = self.client.post(url, data, follow=True)
            msgs = [str(m) for m in resp.context["messages"]]
            self.assertTrue(msgs, data)
            if expected:
                self.assertTrue(any(expected in m for m in msgs), (data, msgs))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEW)

    def test_book_with_naive_datetime_gets_timezone(self):
        order = self.new_order()
        offer = order.offers.get()
        order_services.provider_respond(offer, accept=True, comment="", price_min=300, price_max=500,
                                        term_note="завтра", by=self.w["provider"].owner or self.w["client"])
        resp = self.client.post(reverse("web:order_action", args=[order.pk]),
                                {"action": "book", "offer": offer.pk, "scheduled_at": "2030-01-15T10:30"}, follow=True)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.BOOKED)
        self.assertIsNotNone(order.scheduled_at)
        self.assertEqual(resp.status_code, 200)

    def test_review_for_unfinished_order_is_refused(self):
        order = self.new_order()
        self.client.post(reverse("web:order_action", args=[order.pk]), {"action": "review", "rating": "5"})
        self.assertFalse(Review.objects.filter(order=order).exists())

    # ---------- гараж ----------

    def test_garage_rejects_duplicate_vin(self):
        vin = "WVWZZZ1KZAW123456"
        Vehicle.objects.create(owner=self.w["client"], vin=vin, model_name="Golf")
        resp = self.client.post(reverse("web:garage"), {"vin": vin, "model_name": "Golf"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "уже є у вашому гаражі")
        self.assertEqual(Vehicle.objects.filter(vin=vin).count(), 1)

    def test_record_with_invalid_data_is_rejected_and_mileage_grows(self):
        vehicle = self.w["vehicle"]
        url = reverse("web:vehicle_record_add", args=[vehicle.pk])
        resp = self.client.post(url, {"kind": "service", "title": "", "date": "не дата"}, follow=True)
        self.assertContains(resp, "Перевірте поля запису")
        self.assertEqual(GarageRecord.objects.count(), 0)

        self.client.post(url, {"kind": "service", "title": "Заміна масла", "date": "2026-10-15", "mileage": 160000})
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 160000, "більший пробіг із запису оновлює авто")
        self.client.post(url, {"kind": "service", "title": "Фільтр", "date": "2026-10-16", "mileage": 100})
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 160000, "менший пробіг не зменшує")

    def test_receipt_upload_validation_limit_success_and_failure(self):
        vehicle = self.w["vehicle"]
        url = reverse("web:vehicle_receipt_add", args=[vehicle.pk])

        resp = self.client.post(url, {}, follow=True)
        self.assertContains(resp, "Завантажте фото чека")

        parsed = {"date": "2026-10-15", "mileage": 121340, "total": 2320, "title": "Заміна масла", "kind": "service"}
        with mock.patch("apps.garage.services.parse_receipt_with_gemini", return_value=parsed):
            resp = self.client.post(url, {"image": png("ok.png")}, follow=True)
        self.assertContains(resp, "Чек розпізнано")
        self.assertEqual(GarageRecord.objects.get().amount, 2320)

        with mock.patch("apps.garage.services.parse_receipt_with_gemini", side_effect=RuntimeError("api down")), \
                self.assertLogs("apps.garage.services", "ERROR"):
            resp = self.client.post(url, {"image": png("bad.png")}, follow=True)
        self.assertContains(resp, "Не вдалося розпізнати чек")
        self.assertEqual(Receipt.objects.filter(status=Receipt.Status.FAILED).count(), 1)

        with mock.patch("apps.web.views._rate_limited", return_value=True):
            resp = self.client.post(url, {"image": png("limit.png")}, follow=True)
        self.assertContains(resp, "Ліміт розпізнавання")
        self.assertEqual(Receipt.objects.count(), 2, "понад ліміт чек навіть не зберігається")
