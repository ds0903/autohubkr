from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Provider
from apps.core.testing import make_world
from apps.garage.models import GarageRecord
from apps.orders import services
from apps.orders.models import BypassReport, Offer, Order
from apps.orders.services import OrderError
from apps.reviews import services as review_services
from apps.reviews.models import Review


class DealFlowTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.order = services.create_order(
            client=self.w["client"], city=self.w["city"], description="Стукає спереду", vehicle=self.w["vehicle"],
            service_type=self.w["chassis"], provider=self.w["provider"],
        )

    def _book(self):
        offer = self.order.offers.get()
        services.provider_respond(offer, accept=True, price_min=300, price_max=500, term_note="1 день")
        return services.book_visit(self.order, offer, None, by=self.w["client"])

    def test_create_order_requests_offer_and_logs(self):
        self.assertEqual(self.order.status, Order.Status.NEW)
        self.assertEqual(self.order.offers.count(), 1)
        self.assertEqual(self.order.offers.get().status, Offer.Status.REQUESTED)
        self.assertEqual(self.order.category, self.w["cat"])
        self.assertGreaterEqual(self.order.status_log.count(), 1)

    def test_contacts_hidden_until_booked(self):
        self.assertFalse(self.order.contacts_visible)
        order = self._book()
        self.assertTrue(order.contacts_visible)
        self.assertRegex(order.visit_code, r"^\d{6}$")

    def test_provider_response_moves_to_offered_and_tracks_time(self):
        offer = self.order.offers.get()
        services.provider_respond(offer, accept=True, price_min=300, price_max=500)
        self.order.refresh_from_db()
        offer.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OFFERED)
        self.assertIsNotNone(offer.responded_at)
        self.w["provider"].refresh_from_db()
        self.assertIsNotNone(self.w["provider"].avg_response_minutes)

    def test_visit_code_must_match(self):
        order = self._book()
        with self.assertRaises(OrderError):
            services.confirm_visit(order, "000000" if order.visit_code != "000000" else "111111")
        services.confirm_visit(order, order.visit_code)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.VISITED)

    def test_invalid_transition_rejected(self):
        with self.assertRaises(OrderError):
            services.change_status(self.order, Order.Status.DONE)

    def test_full_deal_creates_confirmed_garage_record_and_review(self):
        order = self._book()
        services.confirm_visit(order, order.visit_code)
        services.propose_estimate(order, "Заміна сайлентблоків", Decimal("1800"))
        services.approve_estimate(order, by=self.w["client"])

        services.confirm_work(order, side=BypassReport.Side.PROVIDER, final_amount=Decimal("1750"), warranty_days=30)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.ESTIMATE, "одна сторона ще не робить угоду")
        self.assertFalse(order.is_confirmed_deal)

        services.confirm_work(order, side=BypassReport.Side.CLIENT)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DONE)
        self.assertTrue(order.is_confirmed_deal)

        record = GarageRecord.objects.get(order=order)
        self.assertEqual(record.trust, GarageRecord.Trust.PROVIDER_CONFIRMED)
        self.assertEqual(record.amount, Decimal("1750"))
        self.w["provider"].refresh_from_db()
        self.assertEqual(self.w["provider"].confirmed_deals_count, 1)

        review = review_services.submit_review(order, 5, "Все чудово")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REVIEWED)
        review_services.moderate(review, approve=True)
        self.w["provider"].refresh_from_db()
        self.assertEqual(self.w["provider"].rating, Decimal("5.00"))
        self.assertEqual(self.w["provider"].reviews_count, 1)

    def test_review_requires_confirmed_deal(self):
        with self.assertRaises(review_services.ReviewError):
            review_services.submit_review(self.order, 5)

    def test_rejected_review_does_not_count(self):
        order = self._book()
        services.confirm_visit(order, order.visit_code)
        services.propose_estimate(order, "x", Decimal("100"))
        services.approve_estimate(order)
        services.confirm_work(order, side=BypassReport.Side.PROVIDER)
        services.confirm_work(order, side=BypassReport.Side.CLIENT)
        review = review_services.submit_review(order, 1, "погано")
        review_services.moderate(review, approve=False, reason="тест")
        provider = Provider.objects.get(pk=self.w["provider"].pk)
        self.assertEqual(provider.reviews_count, 0)
        self.assertEqual(Review.objects.get().status, Review.Status.REJECTED)

    def test_cannot_book_foreign_offer(self):
        other = services.create_order(client=self.w["client"], city=self.w["city"], description="інша",
                                      provider=self.w["provider"])
        with self.assertRaises(OrderError):
            services.book_visit(self.order, other.offers.get(), None)

    def test_pilot_metrics_and_bypass_log(self):
        services.report_bypass(self.order, side="client", reason="price", note="просить без комісії")
        m = services.pilot_metrics()
        self.assertEqual(m["total_orders"], 1)
        self.assertEqual(m["confirmed_deals"], 0)
        self.assertEqual(m["bypass_by_reason"], {"price": 1})
