"""Оформлення адмінки (Jazzmin) і головна сторінка зв'язкового: показники, черга уваги, права доступу."""
import tempfile

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ProviderPhoto
from apps.core.templatetags.admin_dashboard import admin_dashboard_stats
from apps.core.testing import make_world
from apps.orders import services
from apps.orders.models import Offer, Order
from apps.reviews.models import Review


class DashboardTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.admin = User.objects.create_superuser("liaison", password="pass12345", role=User.Role.LIAISON)
        self.order = services.create_order(client=self.w["client"], city=self.w["city"], description="Стукає спереду",
                                           vehicle=self.w["vehicle"], service_type=self.w["chassis"])
        self.client.force_login(self.admin)

    def test_dashboard_uses_theme_and_shows_main_blocks(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertEqual(resp.status_code, 200)
        for text in ("Панель зв'язкового", "Нові заявки", "Потребує уваги", "Швидкі дії", "Останні заявки",
                     "Підтверджені угоди"):
            self.assertContains(resp, text)
        # у бойовому режимі імена файлів мають хеш (admin.<хеш>.css)
        for asset in (r"core/admin(\.[0-9a-f]+)?\.css", r"core/logo(\.[0-9a-f]+)?\.svg", r"core/admin(\.[0-9a-f]+)?\.js"):
            self.assertRegex(resp.content.decode(), asset)
        # Заявка з'являється в таблиці останніх із посиланням на картку
        self.assertContains(resp, reverse("admin:orders_order_change", args=[self.order.pk]))
        self.assertContains(resp, "Діагностика ходової")

    def test_stats_count_what_needs_attention(self):
        s = admin_dashboard_stats()
        self.assertEqual((s["orders_new"], s["orders_active"], s["offers_waiting"]), (1, 1, 0))
        self.assertEqual((s["photos_pending"], s["reviews_pending"], s["deals"]), (0, 0, 0))
        self.assertEqual(s["providers"], 1)
        self.assertEqual(s["clients"], 1)
        self.assertEqual(s["providers_unconfirmed"], 1)

        Offer.objects.create(order=self.order, provider=self.w["provider"], status=Offer.Status.REQUESTED)
        ProviderPhoto.objects.create(provider=self.w["provider"], image="providers/x.jpg")
        Review.objects.create(order=self.order, provider=self.w["provider"], client=self.w["client"], rating=5)
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.DONE)
        s = admin_dashboard_stats()
        self.assertEqual((s["offers_waiting"], s["photos_pending"], s["reviews_pending"]), (1, 1, 1))
        self.assertEqual((s["orders_new"], s["orders_active"]), (0, 0), "виконана заявка не вважається активною")
        self.assertEqual([o.pk for o in s["recent_orders"]], [self.order.pk])

    def test_attention_links_open_valid_filtered_lists(self):
        """Посилання з головної ведуть на списки з фільтром, який адмінка приймає (без редіректу ?e=1)."""
        resp = self.client.get(reverse("admin:index"))
        for name, query in (("admin:orders_order_changelist", "status__exact=new"),
                            ("admin:orders_offer_changelist", "status__exact=requested"),
                            ("admin:reviews_review_changelist", "status__exact=pending"),
                            ("admin:catalog_providerphoto_changelist", "is_approved__exact=0"),
                            ("admin:catalog_provider_changelist", "data_status__exact=collected")):
            url = f"{reverse(name)}?{query}"
            self.assertContains(resp, url.replace("&", "&amp;"))
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_staff_without_permissions_sees_no_business_blocks(self):
        staff = User.objects.create_user("helper", password="pass12345", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("admin:index"))
        self.assertEqual(resp.status_code, 200)
        for hidden in ("Нові заявки", "Останні заявки", "Додати виконавця", "Фото виконавців на модерації"):
            self.assertNotContains(resp, hidden)

    def test_permissions_open_only_matching_blocks(self):
        staff = User.objects.create_user("moderator", password="pass12345", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=["view_review", "view_providerphoto"]))
        self.client.force_login(staff)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Відгуки на модерації")
        self.assertContains(resp, "Фото виконавців на модерації")
        self.assertNotContains(resp, "Нові заявки")
        self.assertNotContains(resp, "Виконавці з неперевіреними даними")

    def test_anonymous_is_sent_to_login_and_login_page_is_themed(self):
        self.client.logout()
        self.assertRedirects(self.client.get(reverse("admin:index")), f"{reverse('admin:login')}?next=/admin/")
        login = self.client.get(reverse("admin:login"))
        self.assertRegex(login.content.decode(), r"core/admin(\.[0-9a-f]+)?\.css")
        self.assertContains(login, "виконавці, заявки та відгуки")

    def test_login_from_the_login_page_lands_in_the_panel(self):
        """Вхід зі сторінки /admin/login/ (без ?next=) веде в панель, а не на головну сайту."""
        self.client.logout()
        form = self.client.get(reverse("admin:login"))
        # форма сама несе next, бо інакше Django веде на LOGIN_REDIRECT_URL (головна сайту)
        self.assertContains(form, '<input type="hidden" name="next" value="/admin/">', html=True)
        resp = self.client.post(reverse("admin:login"), {"username": "liaison", "password": "pass12345", "next": "/admin/"})
        self.assertRedirects(resp, reverse("admin:index"), fetch_redirect_response=False)
        self.client.logout()
        resp = self.client.post(f"{reverse('admin:login')}?next=/admin/catalog/provider/",
                                {"username": "liaison", "password": "pass12345"})
        self.assertRedirects(resp, "/admin/catalog/provider/", fetch_redirect_response=False)

    def test_provider_form_has_short_tabs_and_checkbox_categories(self):
        resp = self.client.get(reverse("admin:catalog_provider_change", args=[self.w["provider"].pk]))
        self.assertContains(resp, 'type="checkbox" name="categories"')
        self.assertNotContains(resp, "Контакти (приховані")
        self.assertContains(resp, "Приховані від клієнта до підтвердження запису.")
        self.assertContains(resp, "Рахується автоматично.")
        # бренди обираються пошуком, а не двома списками з англійськими підказками
        self.assertNotContains(resp, "selector-available")

    def test_sidebar_groups_are_ordered_for_daily_work(self):
        resp = self.client.get(reverse("admin:index"))
        html = resp.content.decode()
        nav = html[html.index('id="jazzy-navigation"'):]
        self.assertLess(nav.index("Заявки"), nav.index("Каталог"))
        self.assertNotContains(resp, "/admin/auth/group/")


class ProductionStaticFilesTests(TestCase):
    """Адмінка з хешованою статикою (DJANGO_DEBUG=0 на сервері).

    Регресія: Jazzmin вставляє {% static 'vendor/bootswatch' %} (каталог), і ManifestStaticFilesStorage кидав
    «Missing staticfiles manifest entry»: вхід на сторінку входу працював, а вся панель після входу відповідала 500.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.static_root = tempfile.mkdtemp()
        cls._override = override_settings(
            STATIC_ROOT=cls.static_root,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "apps.core.storage.TolerantManifestStaticFilesStorage"},
            },
        )
        cls._override.enable()
        call_command("collectstatic", interactive=False, verbosity=0)

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        super().tearDownClass()

    def setUp(self):
        self.w = make_world()
        self.admin = User.objects.create_superuser("liaison", password="pass12345", role=User.Role.LIAISON)
        self.client.force_login(self.admin)

    def test_panel_pages_render_with_hashed_static_files(self):
        provider = self.w["provider"]
        for url in (reverse("admin:index"), reverse("admin:orders_order_changelist"),
                    reverse("admin:catalog_provider_change", args=[provider.pk]),
                    reverse("admin:orders_order_report")):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertRegex(html, r"/static/core/admin\.[0-9a-f]{12}\.css")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)
