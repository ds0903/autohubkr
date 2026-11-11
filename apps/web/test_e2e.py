"""E2E: реальний Chrome (через Playwright) проти живого сервера Django.

Браузери Playwright не завантажуються: тести беруть встановлений Google Chrome (channel="chrome").
Якщо Playwright або Chrome недоступні, тести пропускаються. HTMX підтягується з CDN, тож без інтернету
сценарії з ШІ-формою пропускаються, а решта працює.

Запуск лише E2E:  python manage.py test apps.web.test_e2e
"""
import os
import re
import tempfile
from unittest import SkipTest

# Playwright (sync API) тримає event loop у потоці тесту; Django за замовчуванням забороняє там ORM.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.bot import logic  # noqa: E402
from apps.core.testing import make_world  # noqa: E402
from apps.orders.models import Order  # noqa: E402
from apps.reviews.models import Review  # noqa: E402
from apps.web.views import make_confirm_token  # noqa: E402

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

VIN = "WVWZZZ1KZAW123456"
ADMIN_PASSWORD = "pass12345"


@override_settings(GEMINI_API_KEY="", MEDIA_ROOT=tempfile.mkdtemp(), DEBUG=False)
class BrowserTestCase(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise SkipTest("пакет playwright не встановлено (pip install -r requirements-dev.txt)")
        super().setUpClass()
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # Chrome не встановлено або не запускається
            cls._pw.stop()
            raise SkipTest(f"Google Chrome недоступний для Playwright: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser:
            browser.close()
            cls._pw.stop()
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.js_errors = []
        self.w = make_world()
        self.provider = self.w["provider"]
        self.owner = User.objects.create_user("owner", password="x", role=User.Role.PROVIDER, telegram_id=7001)
        self.provider.owner = self.owner
        self.provider.save()
        self.liaison = User.objects.create_superuser("liaison", password=ADMIN_PASSWORD, role=User.Role.LIAISON,
                                                     telegram_id=7002)

    def tearDown(self):
        self.assertEqual(self.js_errors, [], "у браузері були JS-помилки")

    # ---------- допоміжне ----------

    def url(self, name, *args):
        return self.live_server_url + reverse(name, args=args)

    def new_page(self, viewport=None, reduced_motion=None):
        context = self.browser.new_context(locale="uk-UA", viewport=viewport or {"width": 1280, "height": 900},
                                           reduced_motion=reduced_motion)
        context.set_default_timeout(20000)
        self.addCleanup(context.close)
        page = context.new_page()
        page.on("pageerror", lambda err: self.js_errors.append(str(err)))
        return page

    def login(self, page, username="client1", password="pass12345"):
        page.goto(self.url("web:login"))
        page.fill("#id_username", username)
        page.fill("#id_password", password)
        page.get_by_role("button", name="Увійти").click()
        expect(page.get_by_role("button", name="Вийти")).to_be_visible()

    def admin_login(self, page):
        page.goto(self.live_server_url + "/admin/login/")
        page.fill("input[name=username]", "liaison")
        page.fill("input[name=password]", ADMIN_PASSWORD)
        page.locator("form button[type=submit]").click()
        expect(page).to_have_url(re.compile(r"/admin/$"))

    def need_htmx(self, page):
        try:
            page.wait_for_function("window.htmx !== undefined", timeout=8000)
        except Exception:
            self.skipTest("HTMX із CDN не завантажився (немає інтернету?)")

    def assert_no_horizontal_scroll(self, page, label):
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0, f"{label}: горизонтальний скрол на {overflow}px")


class VisitorJourneyTests(BrowserTestCase):
    def test_visitor_asks_ai_registers_and_creates_order(self):
        page = self.new_page()
        page.goto(self.url("web:home"))
        self.need_htmx(page)
        page.fill("#ai-text", "Стукає підвіска спереду на ямах")
        # авто не обирають зі списку: його просто вписують, навіть якщо такої марки немає в довіднику
        self.assertEqual(page.locator("#ai-car").evaluate("el => el.tagName"), "INPUT")
        page.fill("#ai-car", "Geely Coolray 2021")
        page.get_by_role("button", name="Дізнатись, що це може бути").click()
        result = page.locator("#ai-result")
        expect(result).to_contain_text("припущення")
        expect(result).to_contain_text("СТО Тест")
        expect(result).to_contain_text("300")

        page.get_by_role("link", name="Реєстрація").click()
        page.fill("#id_username", "ivan")
        page.fill("#id_first_name", "Іван")
        page.fill("#id_password1", "S3cure-pass-77")
        page.fill("#id_password2", "S3cure-pass-77")
        page.get_by_role("button", name="Створити акаунт").click()
        # без згоди на обробку даних браузер не відправляє форму (required), реєстрація неможлива
        expect(page).to_have_url(re.compile(r"/accounts/register/"))
        self.assertFalse(User.objects.filter(username="ivan").exists())
        page.check("#id_consent")
        page.get_by_role("button", name="Створити акаунт").click()
        expect(page.get_by_role("button", name="Вийти")).to_be_visible()

        page.goto(self.url("web:order_new") + f"?provider={self.provider.pk}")
        page.fill("#id_description", "Стукає підвіска спереду на ямах")
        page.get_by_role("button", name="Надіслати заявку").click()
        expect(page).to_have_url(re.compile(r"/orders/\d+/$"))
        expect(page.get_by_role("heading", name=re.compile(r"Заявка №\d+"))).to_be_visible()
        expect(page.locator("body")).to_contain_text("Очікуємо відповідь")
        order = Order.objects.get()
        self.assertEqual(order.client.username, "ivan")
        self.assertEqual(order.offers.count(), 1)

    def test_safety_question_shows_warning(self):
        page = self.new_page()
        page.goto(self.url("web:home"))
        self.need_htmx(page)
        page.fill("#ai-text", "Не гальмує, педаль провалюється")
        page.get_by_role("button", name="Дізнатись, що це може бути").click()
        expect(page.locator("#ai-result .danger-box")).to_contain_text("небезпечно")

    def test_catalog_provider_page_hides_contacts_and_has_clean_urls(self):
        page = self.new_page()
        page.goto(self.url("web:catalog"))
        page.get_by_role("link", name="СТО Тест").first.click()
        expect(page.get_by_role("heading", name="СТО Тест").first).to_be_visible()
        expect(page.locator("body")).not_to_contain_text("+380501112233")
        expect(page.locator("body")).to_contain_text("Контакти відкриваються після підтвердження запису")


class DealLifecycleTests(BrowserTestCase):
    def test_full_deal_client_in_browser_provider_in_bot_admin_moderates(self):
        client_page = self.new_page()
        self.login(client_page)

        # 1. Заявка конкретному виконавцю
        client_page.goto(self.url("web:order_new") + f"?provider={self.provider.pk}")
        client_page.fill("#id_description", "Стукає підвіска спереду на ямах")
        client_page.select_option("#id_vehicle", str(self.w["vehicle"].pk))
        client_page.get_by_role("button", name="Надіслати заявку").click()
        expect(client_page).to_have_url(re.compile(r"/orders/\d+/$"))
        order = Order.objects.get()
        offer = order.offers.get()

        # 2. Виконавець відповідає (логіка Telegram-бота), клієнт бачить пропозицію, контактів ще немає
        ok, _ = logic.offer_accept(self.owner, offer.pk, "300-500 грн, 1 день, завтра 10:00")
        self.assertTrue(ok)
        client_page.reload()
        expect(client_page.get_by_role("button", name="Записатись")).to_be_visible()
        expect(client_page.locator("body")).not_to_contain_text("+380501112233")

        # 3. Запис → контакти й код візиту
        client_page.fill("input[name=scheduled_at]", "2026-12-01T10:00")
        client_page.get_by_role("button", name="Записатись").click()
        expect(client_page.locator("body")).to_contain_text("+380501112233")
        order.refresh_from_db()
        expect(client_page.locator(".visit-code")).to_have_text(order.visit_code)

        # 4. Візит за кодом і кошторис; клієнт погоджує
        self.assertTrue(logic.provider_visit(self.owner, order.pk, order.visit_code)[0])
        self.assertTrue(logic.provider_estimate(self.owner, order.pk, "1800", "Заміна сайлентблоків")[0])
        client_page.reload()
        expect(client_page.locator("body")).to_contain_text("Заміна сайлентблоків")
        client_page.get_by_role("button", name="Погоджую кошторис").click()
        expect(client_page.get_by_role("heading", name="Підтвердження виконання")).to_be_visible()

        # 5. Виконавець підтверджує перший; угоди ще немає, відгуку теж
        self.assertTrue(logic.provider_done(self.owner, order.pk, "1900", "30")[0])
        client_page.reload()
        expect(client_page.locator("body")).not_to_contain_text("Залишити відгук")
        client_page.get_by_role("button", name="Підтверджую: роботу виконано").click()
        expect(client_page.locator("body")).to_contain_text("Угода підтверджена обома сторонами")
        expect(client_page.locator("body")).to_contain_text("підтверджено виконавцем")

        # 6. Відгук → на модерацію
        client_page.fill("#id_rating", "5")
        client_page.fill("#id_comment", "Все чітко, без сюрпризів")
        client_page.get_by_role("button", name="Залишити відгук").click()
        expect(client_page.locator("body")).to_contain_text("Відгук надіслано на модерацію")
        self.assertEqual(Review.objects.get().status, Review.Status.PENDING)

        public = self.new_page()
        public.goto(self.url("web:provider", self.provider.pk))
        expect(public.locator("body")).not_to_contain_text("Все чітко, без сюрпризів")

        # 7. Зв'язковий публікує відгук в адмінці
        admin_page = self.new_page()
        self.admin_login(admin_page)
        admin_page.goto(self.live_server_url + "/admin/reviews/review/")
        expect(admin_page.locator("#cmBulk")).to_be_hidden()
        admin_page.locator(".cm-card").first.locator("input[name=_selected_action]").check()
        expect(admin_page.locator("#cmBulk")).to_be_visible()
        admin_page.locator("#cmBulk .cm-select__trigger").click()
        admin_page.locator(".cm-select__menu.is-open .cm-select__item", has_text="Опублікувати вибрані відгуки").click()
        admin_page.locator("button[name=index]").click()
        expect(admin_page.locator(".alert-success")).to_contain_text("Опубліковано: 1")

        public.reload()
        expect(public.locator("body")).to_contain_text("Все чітко, без сюрпризів")

        # 8. Звіт пілота показує угоду
        admin_page.goto(self.live_server_url + "/admin/orders/order/report/")
        expect(admin_page.locator("body")).to_contain_text("Усього заявок")

        # 9. Гараж: запис «підтверджено виконавцем»
        client_page.goto(self.url("web:vehicle", self.w["vehicle"].pk))
        expect(client_page.locator("body")).to_contain_text("підтверджено виконавцем")
        order.refresh_from_db()
        self.assertTrue(order.is_confirmed_deal)

    def test_foreign_order_is_404_in_browser(self):
        order = Order.objects.create(client=self.liaison, city=self.w["city"], description="чужа")
        page = self.new_page()
        self.login(page)
        response = page.goto(self.url("web:order_detail", order.pk))
        self.assertEqual(response.status, 404)


class GarageTests(BrowserTestCase):
    def test_add_car_by_vin_manual_record_and_maintenance_plan(self):
        page = self.new_page()
        self.login(page)
        page.goto(self.url("web:garage"))
        page.fill("#id_vin", VIN)
        page.fill("#id_model_name", "Golf")
        page.fill("#id_mileage", "90000")
        page.get_by_role("button", name="Додати в гараж").click()
        expect(page).to_have_url(re.compile(r"/garage/\d+/$"))
        expect(page.locator("body")).to_contain_text(f"VIN {VIN}")
        expect(page.locator("body")).to_contain_text("Volkswagen")
        expect(page.locator("body")).to_contain_text("сервісною книжкою")

        page.fill("#id_title", "Заміна моторного мастила")
        page.fill("#id_date", "2026-10-01")
        page.fill("#id_mileage", "95000")
        page.fill("#id_amount", "1200")
        page.get_by_role("button", name="Додати запис").click()
        expect(page.locator("body")).to_contain_text("Заміна моторного мастила")
        expect(page.locator("body")).to_contain_text("зі слів власника")
        expect(page.locator("body")).to_contain_text("1200")

    def test_receipt_upload_without_ai_key_fails_gracefully(self):
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (8, 8), "white").save(buf, "PNG")
        image = os.path.join(tempfile.mkdtemp(), "receipt.png")
        with open(image, "wb") as fh:
            fh.write(buf.getvalue())
        page = self.new_page()
        self.login(page)
        page.goto(self.url("web:vehicle", self.w["vehicle"].pk))
        page.set_input_files("#id_image", image)
        page.get_by_role("button", name="Розпізнати й додати").click()
        expect(page.locator(".alert").first).to_contain_text("Не вдалося розпізнати чек")


class ProviderLinksTests(BrowserTestCase):
    def test_owner_confirms_data_via_signed_link(self):
        page = self.new_page()
        page.goto(self.url("web:provider_confirm", make_confirm_token(self.provider)))
        expect(page.get_by_role("heading", name=re.compile("Підтвердіть дані"))).to_be_visible()
        page.fill("#id_phone", "+380509998877")
        page.fill("#id_address", "вул. Нова, 5")
        page.get_by_role("button", name="Усе вірно, підтверджую").click()
        expect(page.locator("body")).to_contain_text("підтверджено власником")
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.data_status, self.provider.DataStatus.OWNER_CONFIRMED)

    def test_tampered_confirm_link_is_rejected(self):
        page = self.new_page()
        response = page.goto(self.url("web:provider_confirm", make_confirm_token(self.provider) + "x"))
        self.assertEqual(response.status, 410)
        expect(page.locator("body")).to_contain_text("Посилання застаріло")

    def test_personal_link_marks_clients_as_providers_own(self):
        page = self.new_page()
        self.login(page)
        page.goto(self.url("web:referral", self.provider.referral_code))
        expect(page).to_have_url(re.compile(r"/orders/new/\?provider=\d+"))
        page.fill("#id_description", "Приїжджаю до вас постійно, треба ТО")
        page.get_by_role("button", name="Надіслати заявку").click()
        expect(page).to_have_url(re.compile(r"/orders/\d+/$"))
        self.assertEqual(Order.objects.get().lead_origin, Order.LeadOrigin.PROVIDER_OWN)

    def test_qr_image_renders_in_browser(self):
        page = self.new_page()
        response = page.goto(self.url("web:referral_qr", self.provider.referral_code))
        self.assertEqual(response.status, 200)
        self.assertIn("svg", response.headers["content-type"])


class MotionTests(BrowserTestCase):
    """Плавність: поява зі скролом, повага до prefers-reduced-motion, запасний вихід без скрипта."""

    ALL_SETTLED = "document.querySelectorAll('[data-reveal]').length === 0"

    def test_lists_fade_in_while_scrolling_and_then_become_ordinary_elements(self):
        page = self.new_page(viewport={"width": 1280, "height": 500})
        page.goto(self.url("web:home"))
        # список виконавців нижче першого екрана: до прокрутки прихований, але місце під нього є
        self.assertEqual(page.evaluate("document.documentElement.classList.contains('js')"), True)
        self.assertGreater(page.evaluate("document.querySelectorAll('[data-reveal]').length"), 0)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_function(self.ALL_SETTLED, timeout=8000)
        expect(page.locator(".steps li").first).to_have_css("opacity", "1")
        expect(page.locator(".provider").first).to_have_css("opacity", "1")
        expect(page.locator(".provider").first).to_have_css("transform", "none")

    def test_reduced_motion_shows_everything_at_once(self):
        page = self.new_page(reduced_motion="reduce")
        page.goto(self.url("web:home"))
        page.wait_for_function(self.ALL_SETTLED, timeout=5000)
        expect(page.locator(".steps li").last).to_have_css("opacity", "1")

    def test_if_motion_script_fails_to_load_content_is_still_shown(self):
        page = self.new_page()
        page.route("**/motion.js", lambda route: route.abort())
        page.goto(self.url("web:home"))
        page.wait_for_function("!document.documentElement.classList.contains('js')", timeout=8000)
        expect(page.locator(".steps li").first).to_have_css("opacity", "1")

    def test_wait_indicator_is_faded_out_not_removed_and_page_fades_between_navigations(self):
        page = self.new_page()
        page.goto(self.url("web:home"))
        indicator = page.locator("#ai-wait")
        expect(indicator).to_have_css("opacity", "0")
        self.assertNotEqual(indicator.evaluate("el => getComputedStyle(el).display"), "none")
        page.get_by_role("link", name="Каталог").click()
        expect(page).to_have_url(re.compile(r"/catalog/$"))
        expect(page.get_by_role("heading", name="Каталог виконавців")).to_be_visible()


class PhoneCompactTests(BrowserTestCase):
    """На телефоні поля сторінки 5px, відступ усередині рамок 10px, шапка низька, правила пароля стислі."""

    def px(self, page, selector, prop):
        return page.evaluate("([s, p]) => parseFloat(getComputedStyle(document.querySelector(s))[p])", [selector, prop])

    def test_registration_is_compact_on_a_phone(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        page.goto(self.url("web:register"))
        for selector, limit in (("main .wrap", 5), ("form.card", 10)):
            for side in ("paddingLeft", "paddingRight"):
                self.assertLessEqual(self.px(page, selector, side), limit, f"{selector} {side}")
        self.assertLessEqual(page.locator("header.top").evaluate("el => el.offsetHeight"), 100, "шапка не вища за два рядки")
        # правила пароля: дрібний суцільний текст, а не список із великими маркерами
        self.assertLessEqual(self.px(page, "form > ul", "fontSize"), 13)
        self.assertEqual(page.locator("form > ul").evaluate("el => getComputedStyle(el).listStyleType"), "none")
        # галочки стоять у рядок із підписом: висота рядка згоди менша за два рядки поля
        consent = page.locator("#id_consent")
        self.assertLessEqual(consent.evaluate("el => el.parentElement.offsetHeight"), 80)
        self.assert_no_horizontal_scroll(page, "register")

    def test_page_height_shrinks_compared_to_desktop_rules(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        page.goto(self.url("web:register"))
        self.assertLessEqual(page.evaluate("document.documentElement.scrollHeight"), 1050)

    def test_provider_order_button_has_room_before_the_badges(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        page.goto(self.url("web:provider", self.provider.pk))
        button = page.locator(".provider-head a.btn").bounding_box()
        badge = page.locator(".badge").first.bounding_box()
        self.assertGreaterEqual(badge["y"] - (button["y"] + button["height"]), 24, "кнопка не липне до значків")
        self.assert_no_horizontal_scroll(page, "provider")

    def test_home_form_keeps_compact_gutters_on_a_phone(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        page.goto(self.url("web:home"))
        for side in ("paddingLeft", "paddingRight"):
            self.assertLessEqual(self.px(page, "form.intake", side), 10)
        self.assertEqual(page.locator("form.intake").evaluate("el => el.getBoundingClientRect().left"), 5)
        self.assert_no_horizontal_scroll(page, "home")


class ResponsiveAndAdminTests(BrowserTestCase):
    def test_public_pages_fit_phone_screen(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        for name, args in (("web:home", ()), ("web:catalog", ()), ("web:provider", (self.provider.pk,)),
                           ("web:login", ()), ("web:register", ()), ("web:privacy", ())):
            page.goto(self.url(name, *args))
            self.assert_no_horizontal_scroll(page, name)

    def test_private_pages_fit_phone_screen(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        self.login(page)
        order = Order.objects.create(client=self.w["client"], city=self.w["city"], description="Тест")
        for name, args in (("web:garage", ()), ("web:vehicle", (self.w["vehicle"].pk,)), ("web:orders", ()),
                           ("web:order_new", ()), ("web:order_detail", (order.pk,)), ("web:profile", ())):
            page.goto(self.url(name, *args))
            self.assert_no_horizontal_scroll(page, name)

    def test_admin_sections_open_without_errors(self):
        page = self.new_page()
        self.admin_login(page)
        for path in ("orders/order/", "orders/offer/", "orders/bypassreport/", "catalog/provider/", "reviews/review/",
                     "garage/vehicle/", "accounts/user/", "assistant/conversation/", "core/notification/",
                     "orders/order/report/", f"orders/order/add/", f"catalog/provider/{self.provider.pk}/change/"):
            response = page.goto(f"{self.live_server_url}/admin/{path}")
            self.assertEqual(response.status, 200, path)

    def test_admin_theme_colours_and_ukrainian_labels(self):
        page = self.new_page()
        self.admin_login(page)
        expect(page.locator("h1").first).to_have_text("Панель зв'язкового")
        # to_have_css повторює перевірку, поки закінчаться плавні переходи кольору
        expect(page.locator("#jazzy-navbar")).to_have_css("background-color", "rgb(11, 74, 143)")
        expect(page.locator("#jazzy-sidebar")).to_have_css("background-color", "rgb(10, 37, 64)")
        expect(page.locator("#jazzy-navigation .nav-link.active")).to_have_css("background-color", "rgb(255, 200, 0)")
        expect(page.locator("#jazzy-navigation")).to_contain_text("Головна")
        expect(page.locator("body")).not_to_contain_text("Dashboard")
        expect(page.locator(".app-footer")).not_to_contain_text("Jazzmin")
        expect(page.locator(".app-footer")).to_contain_text("АвтоХаб КР")
        expect(page.locator(".stat-card")).to_have_count(4)
        # з картки показника можна перейти до відфільтрованого списку
        page.locator(".stat-card--signal").click()
        expect(page).to_have_url(re.compile(r"/admin/orders/order/\?status__exact=new"))
        expect(page).to_have_title(re.compile(r"^Заявки \|"))  # список відкрився, а не сторінка помилки
        expect(page.locator(".cm-page h1")).to_contain_text("Заявки")
        expect(page.locator(".cm-tab.is-active")).to_have_text("Заявка")  # фільтр «Нові» підсвічено вкладкою

    def test_admin_dashboard_and_forms_fit_a_phone(self):
        page = self.new_page(viewport={"width": 375, "height": 812})
        self.admin_login(page)
        for path in ("", "catalog/provider/", f"catalog/provider/{self.provider.pk}/change/", "orders/order/report/",
                     "orders/order/", "orders/order/add/", "reviews/review/", "accounts/user/",
                     "garage/vehicle/", "orders/offer/add/"):
            page.goto(f"{self.live_server_url}/admin/{path}")
            self.assert_no_horizontal_scroll(page, f"admin/{path}")

    def test_admin_lists_are_cards_and_every_dropdown_is_custom(self):
        Order.objects.create(client=self.w["client"], city=self.w["city"], description="Стукає спереду")
        page = self.new_page()
        self.admin_login(page)

        # список: картки замість таблиці
        page.goto(f"{self.live_server_url}/admin/orders/order/")
        expect(page.locator(".cm-card")).to_have_count(Order.objects.count())
        expect(page.locator("table#result_list, .select2-container")).to_have_count(0)
        expect(page.locator(".cm-tab.is-active")).to_have_text("Всі")

        # фільтр: власна панель і власний випадний список, а не вбудований <select>
        page.locator(".cm-filters__trigger").click()
        expect(page.locator("#cmFilterForm")).to_be_visible()
        page.locator("#cmFilterForm .cm-select__trigger").first.click()
        menu = page.locator(".cm-select__menu.is-open")
        expect(menu).to_be_visible()
        expect(menu.locator(".cm-select__item").first).to_be_visible()
        menu.locator(".cm-select__item", has_text="Сайт").click()
        page.locator("#cmFilterForm button[type=submit]").click()
        expect(page).to_have_url(re.compile(r"source__exact=site"))
        expect(page.locator(".cm-card")).to_have_count(Order.objects.filter(source="site").count())
        expect(page.locator(".cm-filters__count")).to_have_text("1")

        # вкладка статусу відкриває відфільтрований список
        page.goto(f"{self.live_server_url}/admin/orders/order/")
        page.locator(".cm-tab", has_text="Заявка").first.click()
        expect(page).to_have_url(re.compile(r"status__exact=new"))
        expect(page.locator(".cm-card")).to_have_count(Order.objects.filter(status="new").count())

        # форма: усі <select> сховані, замість них кнопки власного списку
        page.goto(f"{self.live_server_url}/admin/orders/order/add/")
        natives = page.locator("select:not(.cm-select-native)")
        visible_native = [natives.nth(i) for i in range(natives.count()) if natives.nth(i).is_visible()]
        self.assertEqual(visible_native, [], "на формі лишився вбудований випадний список")
        city = page.locator("#id_city + .cm-select .cm-select__trigger")
        city.click()
        page.locator(".cm-select__menu.is-open .cm-select__item", has_text=self.w["city"].name).click()
        self.assertEqual(page.locator("#id_city").input_value(), str(self.w["city"].pk))
        expect(city).to_contain_text(self.w["city"].name)

    def test_admin_toggle_switches_a_provider_without_opening_the_form(self):
        page = self.new_page()
        self.admin_login(page)
        page.goto(f"{self.live_server_url}/admin/catalog/provider/")
        toggle = page.locator(".cm-card", has_text=self.provider.name).locator(".cm-state")
        expect(toggle).to_have_text("У каталозі")
        toggle.click()
        expect(toggle).to_have_text("Прихований")
        self.provider.refresh_from_db()
        self.assertFalse(self.provider.is_active)
        toggle.click()
        expect(toggle).to_have_text("У каталозі")
        self.provider.refresh_from_db()
        self.assertTrue(self.provider.is_active)

    def test_anonymous_cannot_reach_admin_or_private_pages(self):
        page = self.new_page()
        page.goto(self.live_server_url + "/admin/orders/order/")
        expect(page).to_have_url(re.compile(r"/admin/login/"))
        page.goto(self.url("web:garage"))
        expect(page).to_have_url(re.compile(r"/accounts/login/"))
