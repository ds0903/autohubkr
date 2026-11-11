"""Наскрізний інтеграційний сценарій без браузера: сайт + ШІ + гараж + логіка бота + адмінка + відгуки + сповіщення.

Один клієнт і один виконавець проходять весь шлях із ТЗ: каталог → заявка → пропозиція → запис → візит за кодом →
кошторис → виконання → підтвердження обох сторін → гараж → відгук → модерація → рейтинг.
"""
import tempfile
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.bot import logic
from apps.core.models import Notification
from apps.core.testing import make_world
from apps.orders import services
from apps.orders.models import Order
from apps.reviews.models import Review

VIN = "WVWZZZ1KZAW123456"


@override_settings(GEMINI_API_KEY="", MEDIA_ROOT=tempfile.mkdtemp())
class FullDealIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.w = make_world()
        self.provider = self.w["provider"]
        self.owner = User.objects.create_user("owner", password="x", role=User.Role.PROVIDER, telegram_id=5001)
        self.provider.owner = self.owner
        self.provider.save()
        self.liaison = User.objects.create_superuser("liaison", password="pass12345", role=User.Role.LIAISON,
                                                     telegram_id=5002)

    def texts(self, user):
        return [n.text for n in Notification.objects.filter(user=user)]

    def test_site_client_and_bot_provider_complete_confirmed_deal(self):
        # 1. Реєстрація зі згодою → гараж із VIN
        resp = self.client.post(reverse("web:register"), {
            "username": "ivan", "first_name": "Іван", "phone": "+380671234567",
            "password1": "S3cure-pass-77", "password2": "S3cure-pass-77", "consent": "on"})
        self.assertEqual(resp.status_code, 302)
        ivan = User.objects.get(username="ivan")
        self.assertTrue(self.client.login(username="ivan", password="S3cure-pass-77"))
        resp = self.client.post(reverse("web:garage"), {"vin": VIN, "model_name": "Golf", "mileage": 120000})
        vehicle = ivan.vehicles.get(vin=VIN)
        self.assertRedirects(resp, reverse("web:vehicle", args=[vehicle.pk]))

        # 2. Помічник: гіпотеза, а не діагноз, виконавець із бази, ціни лише з прайсу
        reply = self.client.post(reverse("web:ask"), {"text": "стукає підвіска спереду на ямах"})
        self.assertContains(reply, "Це припущення")
        self.assertContains(reply, "СТО Тест")
        self.assertContains(reply, "300")

        # 3. Заявка конкретному виконавцю → виконавець отримує запит у Telegram
        resp = self.client.post(reverse("web:order_new") + f"?provider={self.provider.pk}", {
            "description": "Стукає підвіска спереду на ямах", "vehicle": vehicle.pk,
            "service_type": self.w["chassis"].pk, "preferred_time": "завтра зранку"})
        order = Order.objects.get()
        self.assertRedirects(resp, reverse("web:order_detail", args=[order.pk]))
        offer = order.offers.get()
        self.assertTrue(any("Нова заявка" in t or f"№{order.pk}" in t for t in self.texts(self.owner)),
                        "виконавець має отримати запит")

        # 4. Виконавець відповідає в боті текстом «300-500 грн, 1 день»
        ok, _ = logic.offer_accept(self.owner, offer.pk, "300-500 грн, 1 день, завтра 10:00")
        self.assertTrue(ok)
        order.refresh_from_db()
        offer.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OFFERED)
        # залишок тексту зберігається один раз, а не тричі в різних полях
        self.assertEqual((offer.term_note, offer.suggested_time, offer.comment), ("1 день, завтра 10:00", "", ""))
        page = self.client.get(reverse("web:order_detail", args=[order.pk]))
        self.assertContains(page, "Записатись")
        self.assertNotContains(page, "+380501112233", msg_prefix="контакти закриті до запису")

        # 5. Клієнт записується на сайті → контакти й код візиту відкриті
        resp = self.client.post(reverse("web:order_action", args=[order.pk]),
                                {"action": "book", "offer": offer.pk, "scheduled_at": "2026-12-01T10:00"}, follow=True)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.BOOKED)
        self.assertContains(resp, "+380501112233")
        self.assertContains(resp, order.visit_code)

        # 6. Хибний код не підтверджує візит; вірний — підтверджує
        ok, msg = logic.provider_visit(self.owner, order.pk, "000000" if order.visit_code != "000000" else "111111")
        self.assertFalse(ok)
        ok, _ = logic.provider_visit(self.owner, order.pk, order.visit_code)
        self.assertTrue(ok)

        # 7. Кошторис → погодження клієнтом на сайті
        ok, _ = logic.provider_estimate(self.owner, order.pk, "1800", "Заміна сайлентблоків")
        self.assertTrue(ok)
        self.assertTrue(any("1800" in t for t in self.texts(ivan)) or ivan.telegram_id is None)
        self.client.post(reverse("web:order_action", args=[order.pk]), {"action": "approve_estimate"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.ESTIMATE)

        # 8. Одностороннє підтвердження — ще не угода, відгук недоступний
        ok, _ = logic.provider_done(self.owner, order.pk, "1900", "30")
        self.assertTrue(ok)
        order.refresh_from_db()
        self.assertFalse(order.is_confirmed_deal)
        self.assertEqual(order.status, Order.Status.ESTIMATE)
        resp = self.client.post(reverse("web:order_action", args=[order.pk]),
                                {"action": "review", "rating": 5, "comment": "ще рано"}, follow=True)
        self.assertEqual(Review.objects.count(), 0)

        # 9. Клієнт підтверджує → угода, запис у гаражі, лічильник виконавця
        self.client.post(reverse("web:order_action", args=[order.pk]), {"action": "confirm_work"})
        order.refresh_from_db()
        self.provider.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DONE)
        self.assertTrue(order.is_confirmed_deal)
        self.assertEqual(order.final_amount, Decimal("1900"))
        self.assertEqual(order.warranty_days, 30)
        self.assertEqual(self.provider.confirmed_deals_count, 1)
        done_page = self.client.get(reverse("web:order_detail", args=[order.pk])).content.decode()
        self.assertIn("гарантія: 30 дн.", done_page)
        self.assertNotIn("дн..", done_page, "подвійна крапка в тексті гарантії")
        self.assertNotIn("Підтвердження: client", done_page, "внутрішні коди не мають потрапляти до клієнта")
        self.assertIn("Роботу підтвердив: виконавець", done_page)
        self.assertNotIn(" : ", done_page.split("Історія заявки")[1], "пробіл перед двокрапкою в історії")
        garage_page = self.client.get(reverse("web:vehicle", args=[vehicle.pk]))
        self.assertContains(garage_page, "підтверджено виконавцем")
        self.assertContains(garage_page, "1900")

        # 10. Відгук → на модерацію, у рейтинг не йде
        self.client.post(reverse("web:order_action", args=[order.pk]),
                         {"action": "review", "rating": 5, "comment": "Все чітко"})
        review = Review.objects.get()
        self.assertEqual(review.status, Review.Status.PENDING)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.reviews_count, 0)
        self.assertNotContains(self.client.get(reverse("web:provider", args=[self.provider.pk])), "Все чітко")

        # 11. Зв'язковий публікує відгук в адмінці → рейтинг і картка оновились
        self.client.force_login(self.liaison)
        self.client.post(reverse("admin:reviews_review_changelist"),
                         {"action": "approve_selected", "_selected_action": [review.pk]}, follow=True)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.reviews_count, 1)
        self.assertEqual(self.provider.rating, Decimal("5.00"))
        self.assertContains(self.client.get(reverse("web:provider", args=[self.provider.pk])), "Все чітко")

        # 12. Звіт пілота бачить угоду, журнал статусів повний
        report = self.client.get(reverse("admin:orders_order_report"))
        self.assertContains(report, "Усього заявок")
        metrics = services.pilot_metrics()
        self.assertEqual((metrics["total_orders"], metrics["confirmed_deals"]), (1, 1))
        steps = list(order.status_log.values_list("to_status", flat=True))
        for status in (Order.Status.NEW, Order.Status.OFFERED, Order.Status.BOOKED, Order.Status.VISITED,
                       Order.Status.ESTIMATE, Order.Status.DONE):
            self.assertIn(status, steps)

    def test_other_user_cannot_touch_deal_via_bot_logic(self):
        order = services.create_order(client=self.w["client"], city=self.w["city"], description="Тест",
                                      provider=self.provider, service_type=self.w["chassis"])
        offer = order.offers.get()
        stranger = User.objects.create_user("stranger", password="x", telegram_id=5003)
        self.assertFalse(logic.offer_accept(stranger, offer.pk, "100 грн")[0])
        self.assertFalse(logic.book_offer(stranger, offer.pk)[0])
        self.assertFalse(logic.cancel_order(stranger, order.pk)[0])
        self.assertFalse(logic.provider_done(stranger, order.pk)[0])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEW)

    def test_cancelled_order_never_becomes_deal(self):
        self.client.login(username="client1", password="pass12345")
        order = services.create_order(client=self.w["client"], city=self.w["city"], description="Тест",
                                      provider=self.provider)
        self.client.post(reverse("web:order_action", args=[order.pk]), {"action": "cancel", "reason": "передумав"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        ok, _ = logic.client_confirm(self.w["client"], order.pk)
        self.assertFalse(ok)
        self.assertEqual(Review.objects.count(), 0)
