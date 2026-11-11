"""Оформлення адмінки (Jazzmin) і головна сторінка зв'язкового: показники, черга уваги, права доступу."""
from django.contrib.auth.models import Permission
from django.test import TestCase
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
                     "Підтверджені угоди", "core/admin.css", "core/logo.svg", "core/admin.js"):
            self.assertContains(resp, text)
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
        self.assertContains(login, "core/admin.css")
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
