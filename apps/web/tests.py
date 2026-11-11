import tempfile

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.core.testing import make_world
from apps.orders import services
from apps.orders.models import Order


@override_settings(GEMINI_API_KEY="", MEDIA_ROOT=tempfile.mkdtemp())
class WebTests(TestCase):
    def setUp(self):
        cache.clear()
        self.w = make_world()

    def login(self):
        self.client.login(username="client1", password="pass12345")

    def test_public_pages_ok(self):
        for name in ("web:home", "web:catalog", "web:privacy", "web:login", "web:register"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertContains(self.client.get(reverse("web:catalog")), "СТО Тест")

    def test_catalog_filters(self):
        url = reverse("web:catalog")
        self.assertContains(self.client.get(url, {"q": "Тест"}), "СТО Тест")
        self.assertNotContains(self.client.get(url, {"q": "неіснуюче"}), "СТО Тест")
        self.assertContains(self.client.get(url, {"category": "sto"}), "СТО Тест")

    def test_provider_page_hides_contacts(self):
        resp = self.client.get(reverse("web:provider", args=[self.w["provider"].pk]))
        self.assertContains(resp, "Діагностика ходової")
        self.assertNotContains(resp, "+380501112233")
        self.assertContains(resp, "Контакти відкриваються після підтвердження запису")

    def test_protected_pages_redirect_anonymous(self):
        for name in ("web:orders", "web:order_new", "web:garage", "web:profile"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn("/accounts/login/", resp["Location"])

    def test_registration_requires_consent(self):
        data = {"username": "newuser", "first_name": "Іра", "password1": "S3cure-pass-77", "password2": "S3cure-pass-77"}
        self.assertEqual(self.client.post(reverse("web:register"), data).status_code, 200)
        self.assertFalse(User.objects.filter(username="newuser").exists())
        resp = self.client.post(reverse("web:register"), {**data, "consent": "on"})
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="newuser")
        self.assertIsNotNone(user.personal_data_consent_at)
        self.assertFalse(user.ai_dialog_consent)
        self.assertEqual(user.role, User.Role.CLIENT)

    def test_ai_endpoint_returns_providers_and_safety(self):
        resp = self.client.post(reverse("web:ask"), {"text": "Не гальмує, скрипять колодки"})
        self.assertContains(resp, "небезпечно")
        self.assertContains(resp, "Це припущення")
        resp = self.client.post(reverse("web:ask"), {"text": "стукає підвіска спереду"})
        self.assertContains(resp, "СТО Тест")
        self.assertContains(resp, "300")

    @override_settings(GEMINI_API_KEY="test-key")
    def test_ai_off_topic_gets_no_providers_and_no_order_prompt(self):
        from unittest import mock

        from apps.assistant import services as ai

        fake = {"possible_causes": [], "what_to_do": "Опишіть симптоми несправності.", "service_type_slug": None,
                "clarifying_questions": ["Що саме не так з авто?"], "is_safety_related": False}
        with mock.patch.object(ai, "_call_llm", return_value=fake):
            resp = self.client.post(reverse("web:ask"), {"text": "Яка столиця Франції? Напиши вірш"})
        self.assertContains(resp, "Опишіть симптоми")
        self.assertNotContains(resp, "СТО Тест")
        self.assertNotContains(resp, "До кого звернутись")
        self.assertNotContains(resp, "Підходящих виконавців у базі поки немає")

    def test_ai_endpoint_validates_and_rate_limits(self):
        self.assertContains(self.client.post(reverse("web:ask"), {"text": "ну"}), "докладніше")
        for _ in range(15):
            self.client.post(reverse("web:ask"), {"text": "стукає підвіска спереду"})
        self.assertContains(self.client.post(reverse("web:ask"), {"text": "стукає підвіска спереду"}), "Забагато запитів")

    def test_order_create_and_isolation(self):
        self.login()
        resp = self.client.post(reverse("web:order_new") + f"?provider={self.w['provider'].pk}", {
            "description": "Стукає спереду на ямах", "vehicle": self.w["vehicle"].pk,
            "service_type": self.w["chassis"].pk, "preferred_time": "завтра",
        })
        order = Order.objects.get()
        self.assertRedirects(resp, reverse("web:order_detail", args=[order.pk]))
        self.assertEqual(order.offers.count(), 1)
        self.assertEqual(order.source, Order.Source.SITE)

        User.objects.create_user("stranger", password="pass12345")
        self.client.logout()
        self.client.login(username="stranger", password="pass12345")
        self.assertEqual(self.client.get(reverse("web:order_detail", args=[order.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("web:order_action", args=[order.pk]), {"action": "cancel"}).status_code, 404)

    def test_contacts_revealed_only_after_booking(self):
        self.login()
        order = services.create_order(client=self.w["client"], city=self.w["city"], description="Стукає",
                                      provider=self.w["provider"], service_type=self.w["chassis"])
        url = reverse("web:order_detail", args=[order.pk])
        self.assertNotContains(self.client.get(url), "+380501112233")

        offer = order.offers.get()
        services.provider_respond(offer, accept=True, price_min=300, price_max=500)
        resp = self.client.get(url)
        self.assertContains(resp, "Записатись")
        self.assertNotContains(resp, "+380501112233")

        resp = self.client.post(reverse("web:order_action", args=[order.pk]),
                                {"action": "book", "offer": offer.pk, "scheduled_at": "2026-12-01T10:00"}, follow=True)
        self.assertContains(resp, "+380501112233")
        order.refresh_from_db()
        self.assertContains(resp, order.visit_code)

    def test_garage_add_vehicle_with_vin_and_record(self):
        self.login()
        resp = self.client.post(reverse("web:garage"), {"nickname": "", "vin": "WVWZZZ1KZAW123456", "model_name": "Golf",
                                                        "mileage": 90000})
        vehicle = self.w["client"].vehicles.get(vin="WVWZZZ1KZAW123456")
        self.assertRedirects(resp, reverse("web:vehicle", args=[vehicle.pk]))
        self.assertEqual(str(vehicle.brand), "Volkswagen")

        resp = self.client.post(reverse("web:vehicle_record_add", args=[vehicle.pk]), {
            "kind": "service", "title": "Заміна мастила", "date": "2026-10-01", "mileage": 95000, "amount": "1200"})
        self.assertEqual(resp.status_code, 302)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 95000, "пробіг автомобіля підтягується із запису")
        page = self.client.get(reverse("web:vehicle", args=[vehicle.pk]))
        self.assertContains(page, "зі слів власника")
        self.assertContains(page, "сервісною книжкою")

    def test_referral_link_marks_order_as_provider_own_client(self):
        provider = self.w["provider"]
        self.login()
        resp = self.client.get(reverse("web:referral", args=[provider.referral_code]))
        self.assertRedirects(resp, f"{reverse('web:order_new')}?provider={provider.pk}")
        self.client.post(f"{reverse('web:order_new')}?provider={provider.pk}", {"description": "Стукає підвіска спереду"})
        self.assertEqual(Order.objects.get().lead_origin, Order.LeadOrigin.PROVIDER_OWN)

    def test_regular_order_is_platform_lead(self):
        self.login()
        self.client.post(f"{reverse('web:order_new')}?provider={self.w['provider'].pk}",
                         {"description": "Стукає підвіска спереду"})
        self.assertEqual(Order.objects.get().lead_origin, Order.LeadOrigin.PLATFORM)

    def test_unknown_referral_code_is_404(self):
        self.assertEqual(self.client.get(reverse("web:referral", args=["nonexist"])).status_code, 404)

    def test_referral_qr_is_svg(self):
        resp = self.client.get(reverse("web:referral_qr", args=[self.w["provider"].referral_code]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", resp.content)

    def test_owner_confirms_provider_data(self):
        from apps.web.views import make_confirm_token

        provider = self.w["provider"]
        url = reverse("web:provider_confirm", args=[make_confirm_token(provider)])
        self.assertContains(self.client.get(url), "СТО Тест")
        resp = self.client.post(url, {"phone": "+380509998877", "telegram_username": "", "address": "вул. Нова, 5",
                                      "district": "Тернівський", "working_hours": "Пн-Сб 9-19", "description": ""})
        self.assertContains(resp, "підтверджено власником")
        provider.refresh_from_db()
        self.assertEqual(provider.data_status, provider.DataStatus.OWNER_CONFIRMED)
        self.assertEqual(provider.phone, "+380509998877")

    def test_bad_confirm_token_is_gone(self):
        self.assertEqual(self.client.get(reverse("web:provider_confirm", args=["garbage"])).status_code, 410)

    def test_sitemap_and_robots(self):
        site = self.client.get(reverse("web:sitemap")).content.decode()
        self.assertIn(reverse("web:catalog"), site)
        self.assertIn(reverse("web:provider", args=[self.w["provider"].pk]), site)
        robots = self.client.get(reverse("web:robots")).content.decode()
        self.assertIn("Disallow: /admin/", robots)
        self.assertIn("Sitemap:", robots)

    # ---- знахідки ручного проходу в браузері ----

    def test_record_form_date_is_prefilled_in_iso_format(self):
        """type="date" ігнорує локалізований «02.11.2026»: поле виглядало порожнім."""
        self.login()
        page = self.client.get(reverse("web:vehicle", args=[self.w["vehicle"].pk])).content.decode()
        self.assertRegex(page, r'name="date"[^>]*value="\d{4}-\d{2}-\d{2}"')

    def test_order_form_preselects_latest_vehicle(self):
        self.login()
        page = self.client.get(reverse("web:order_new")).content.decode()
        self.assertRegex(page, rf'<option value="{self.w["vehicle"].pk}" selected>')

    def test_register_returns_to_intended_page(self):
        data = {"username": "newuser", "first_name": "Іра", "password1": "S3cure-pass-77",
                "password2": "S3cure-pass-77", "consent": "on"}
        target = f"{reverse('web:order_new')}?provider={self.w['provider'].pk}"
        page = self.client.get(reverse("web:register"), {"next": target})
        self.assertContains(page, 'name="next"')
        resp = self.client.post(reverse("web:register"), {**data, "next": target})
        self.assertRedirects(resp, target, fetch_redirect_response=False)

    def test_register_ignores_foreign_next(self):
        data = {"username": "newuser2", "first_name": "Іра", "password1": "S3cure-pass-77",
                "password2": "S3cure-pass-77", "consent": "on"}
        resp = self.client.post(reverse("web:register"), {**data, "next": "https://evil.example/steal"})
        self.assertRedirects(resp, reverse("web:garage"), fetch_redirect_response=False)

    def test_login_page_links_to_register_keeping_next(self):
        page = self.client.get(reverse("web:login"), {"next": "/orders/new/"})
        self.assertContains(page, "/accounts/register/?next=")

    def test_pages_declare_favicon(self):
        self.assertContains(self.client.get(reverse("web:home")), 'rel="icon"')

    def test_home_copy_does_not_decline_city_wrongly(self):
        self.assertNotContains(self.client.get(reverse("web:home")), "у Кривий Ріг")

    def test_cannot_open_foreign_vehicle(self):
        User.objects.create_user("stranger", password="pass12345")
        self.client.login(username="stranger", password="pass12345")
        self.assertEqual(self.client.get(reverse("web:vehicle", args=[self.w["vehicle"].pk])).status_code, 404)

    def test_receipt_without_gemini_key_fails_gracefully(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        import io

        from PIL import Image

        self.login()
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "white").save(buf, "PNG")
        resp = self.client.post(reverse("web:vehicle_receipt_add", args=[self.w["vehicle"].pk]),
                                {"image": SimpleUploadedFile("r.png", buf.getvalue(), content_type="image/png")},
                                follow=True)
        self.assertContains(resp, "Не вдалося розпізнати чек")
