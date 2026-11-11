"""Відгуки: тільки за підтвердженими угодами, з модерацією."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone

from apps.catalog.models import Provider
from apps.orders import services as order_services
from apps.orders.models import Order

from .models import Review


class ReviewError(ValueError):
    pass


@transaction.atomic
def submit_review(order: Order, rating: int, comment: str = "") -> Review:
    if order.status != Order.Status.DONE or not order.is_confirmed_deal:
        raise ReviewError("Відгук можна залишити лише після підтвердженої обома сторонами роботи")
    if hasattr(order, "review"):
        raise ReviewError("Відгук по цій заявці вже залишено")
    if not 1 <= int(rating) <= 5:
        raise ReviewError("Оцінка має бути від 1 до 5")
    review = Review.objects.create(
        order=order, provider=order.provider, client=order.client, rating=int(rating), comment=comment.strip()
    )
    order_services.change_status(order, Order.Status.REVIEWED, by=order.client, note="Клієнт залишив відгук")
    return review


def recalc_provider_rating(provider: Provider) -> None:
    agg = Review.objects.filter(provider=provider, status=Review.Status.APPROVED).aggregate(
        avg=Avg("rating"), n=Count("id")
    )
    provider.rating = Decimal(str(round(agg["avg"] or 0, 2)))
    provider.reviews_count = agg["n"]
    provider.save(update_fields=["rating", "reviews_count", "updated_at"])


@transaction.atomic
def moderate(review: Review, *, approve: bool, by=None, reason: str = "") -> Review:
    review.status = Review.Status.APPROVED if approve else Review.Status.REJECTED
    review.reject_reason = "" if approve else reason[:255]
    review.moderated_by = by
    review.moderated_at = timezone.now()
    review.save()
    recalc_provider_rating(review.provider)
    return review
