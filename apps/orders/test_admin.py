"""Інтеграційні тести адмінки зв'язкового: дії, пропозиції, звіт, модерація відгуків."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.testing import make_world
from apps.orders import services
from apps.orders.models import BypassReport, Offer, Order
from apps.reviews import services as review_services
from apps.reviews.models import Review


class LiaisonAdminTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.owner = User.objects.create_user("owner", password="x", role=User.Role.PROVIDER, telegram_id=555)
        self.w["provider"].owner = self.owner
        self.w["provider"].save()
        self.admin = User.objects.create_superuser("liaison", password="pass12345", role=User.Role.LIAISON, telegram_id=777)
        self.client.force_login(self.admin)
        self.order = services.create_order(client=self.w["client"], city=self.w["city"], description="Стукає спереду",
                                           vehicle=self.w["vehicle"], service_type=self.w["chassis"])

    APP_OF = {"order": "orders", "offer": "orders", "review": "reviews", "provider": "catalog"}

    def act(self, model, action, *objs):
        url = reverse(f"admin:{self.APP_OF[model]}_{model}_changelist")
        return self.client.post(url, {"action": action, "_selected_action": [o.pk for o in objs]}, follow=True)

    def test_changelists_render(self):
        for name in ("admin:orders_order_changelist", "admin:orders_offer_changelist", "admin:orders_bypassreport_changelist",
                     "admin:catalog_provider_changelist", "admin:reviews_review_changelist", "admin:core_notification_changelist",
                     "admin:garage_vehicle_changelist", "admin:accounts_user_changelist", "admin:assistant_conversation_changelist"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertContains(self.client.get(reverse("admin:orders_order_changelist")), "Стукає спереду")

    def test_order_change_form_renders_with_log_and_inlines(self):
        resp = self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))
        self.assertContains(resp, "Заявку створено")
        self.assertContains(resp, "Журнал статусів")

    def test_status_field_is_read_only_in_admin(self):
        """Статус змінюється лише через дії (журнал із відповідальним), а не прямим редагуванням у формі."""
        url = reverse("admin:orders_order_change", args=[self.order.pk])
        self.assertNotIn('name="status"', self.client.get(url).content.decode())

    def test_assign_to_me(self):
        self.act("order", "assign_to_me", self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.liaison, self.admin)

    def test_add_offer_in_admin_sends_request_and_tracks_response(self):
        resp = self.client.post(reverse("admin:orders_offer_add"), {
            "order": self.order.pk, "provider": self.w["provider"].pk, "status": Offer.Status.REQUESTED,
            "price_min": "", "price_max": "", "term_note": "", "suggested_time": "", "comment": ""})
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["adminform"].form.errors)
        offer = Offer.objects.get()
        self.assertTrue(Notification.objects.filter(user=self.owner, kind="offer_request").exists(),
                        "виконавець отримує запит у Telegram")
        # зв'язковий вносить відповідь виконавця, отриману по телефону
        resp = self.client.post(reverse("admin:orders_offer_change", args=[offer.pk]), {
            "order": self.order.pk, "provider": self.w["provider"].pk, "status": Offer.Status.PROPOSED,
            "price_min": "300", "price_max": "500", "term_note": "1 день", "suggested_time": "", "comment": ""})
        self.assertEqual(resp.status_code, 302)
        offer.refresh_from_db()
        self.order.refresh_from_db()
        self.assertIsNotNone(offer.responded_at)
        self.assertEqual(self.order.status, Order.Status.OFFERED)

    def test_full_deal_through_admin_actions(self):
        offer = services.request_offer(self.order, self.w["provider"])
        services.provider_respond(offer, accept=True, price_min=300, price_max=500)

        self.act("offer", "choose_offer", offer)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.BOOKED)
        self.assertTrue(self.order.visit_code)

        self.act("order", "mark_visited", self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.VISITED)

        services.propose_estimate(self.order, "Заміна сайлентблоків", Decimal("1800"))
        self.act("order", "approve_estimate_action", self.order)
        self.act("order", "confirm_by_provider", self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ESTIMATE, "одного підтвердження мало")
        self.act("order", "confirm_by_client", self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DONE)
        self.assertTrue(self.order.is_confirmed_deal)
        # кожен перехід лишив слід у журналі з відповідальним
        self.assertEqual(self.order.status_log.filter(changed_by=self.admin).count() >= 5, True)

    def test_invalid_action_reports_error_not_crash(self):
        resp = self.act("order", "approve_estimate_action", self.order)
        self.assertContains(resp, "Немає кошторису для погодження")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.NEW)

    def test_cancel_action(self):
        self.act("order", "cancel_selected", self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_report_page_counts_deals_and_bypass(self):
        services.report_bypass(self.order, side="provider", reason="price", note="просить без комісії", by=self.admin)
        resp = self.client.get(reverse("admin:orders_order_report"))
        self.assertContains(resp, "Усього заявок")
        self.assertEqual(services.pilot_metrics()["bypass_by_reason"].get("price"), 1)
        # підписи українською, а не внутрішні коди
        self.assertContains(resp, "Хоче дешевше / без комісії")
        self.assertContains(resp, "Заявка")
        self.assertNotContains(resp, "<th>price</th>")
        self.assertNotContains(resp, "<th>new</th>")

    def test_create_order_in_admin_is_logged(self):
        resp = self.client.post(reverse("admin:orders_order_add"), {
            "client": self.w["client"].pk, "city": self.w["city"].pk, "description": "Заявка за дзвінком",
            "source": Order.Source.LIAISON, "lead_origin": Order.LeadOrigin.PLATFORM,
            "warranty_days": "", "price_min": "", "price_max": "", "estimate_amount": "", "final_amount": "",
            # інлайни
            "offers-TOTAL_FORMS": 0, "offers-INITIAL_FORMS": 0, "offers-MIN_NUM_FORMS": 0, "offers-MAX_NUM_FORMS": 1000,
            "bypass_reports-TOTAL_FORMS": 0, "bypass_reports-INITIAL_FORMS": 0, "bypass_reports-MIN_NUM_FORMS": 0,
            "bypass_reports-MAX_NUM_FORMS": 1000,
            "photos-TOTAL_FORMS": 0, "photos-INITIAL_FORMS": 0, "photos-MIN_NUM_FORMS": 0, "photos-MAX_NUM_FORMS": 1000,
            "status_log-TOTAL_FORMS": 0, "status_log-INITIAL_FORMS": 0, "status_log-MIN_NUM_FORMS": 0,
            "status_log-MAX_NUM_FORMS": 1000,
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["adminform"].form.errors)
        created = Order.objects.get(description="Заявка за дзвінком")
        self.assertTrue(created.status_log.filter(note__contains="зв'язковим").exists())

    def test_review_moderation_actions(self):
        offer = services.request_offer(self.order, self.w["provider"])
        services.provider_respond(offer, accept=True, price_min=1, price_max=2)
        services.book_visit(self.order, offer, None)
        services.confirm_visit(self.order, self.order.visit_code)
        services.propose_estimate(self.order, "x", Decimal("100"))
        services.approve_estimate(self.order)
        services.confirm_work(self.order, side=BypassReport.Side.PROVIDER)
        services.confirm_work(self.order, side=BypassReport.Side.CLIENT)
        review = review_services.submit_review(self.order, 4, "Добре")
        self.assertContains(self.client.get(reverse("admin:reviews_review_changelist")), "Добре",
                            msg_prefix="модератор бачить текст відгуку вже в списку")
        self.act("review", "approve_selected", review)
        review.refresh_from_db()
        self.w["provider"].refresh_from_db()
        self.assertEqual(review.status, Review.Status.APPROVED)
        self.assertEqual(review.moderated_by, self.admin)
        self.assertEqual(self.w["provider"].rating, Decimal("4.00"))
        self.act("review", "reject_selected", review)
        self.w["provider"].refresh_from_db()
        self.assertEqual(self.w["provider"].reviews_count, 0, "відхилений відгук не впливає на рейтинг")

    def test_review_cannot_be_added_manually(self):
        self.assertEqual(self.client.get(reverse("admin:reviews_review_add")).status_code, 403)

    def test_provider_links_action_shows_links(self):
        resp = self.act("provider", "show_provider_links", self.w["provider"])
        self.assertContains(resp, "/confirm/")
        self.assertContains(resp, f"/r/{self.w['provider'].referral_code}/")

    def test_non_staff_cannot_open_admin(self):
        self.client.force_login(self.w["client"])
        self.assertEqual(self.client.get(reverse("admin:orders_order_changelist")).status_code, 302)
