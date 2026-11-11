"""Дані для головної сторінки адмінки: що потребує уваги зв'язкового й як іде пілот."""
from datetime import timedelta

from django import template
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Provider, ProviderPhoto
from apps.orders import services
from apps.orders.models import Offer, Order
from apps.reviews.models import Review

register = template.Library()

FINISHED = (Order.Status.DONE, Order.Status.REVIEWED, Order.Status.CANCELLED)


@register.simple_tag
def admin_dashboard_stats() -> dict:
    metrics = services.pilot_metrics()
    week_ago = timezone.now() - timedelta(days=7)
    orders = Order.objects.all()
    providers = Provider.objects.filter(is_active=True)
    return {
        "orders_new": orders.filter(status=Order.Status.NEW).count(),
        "orders_active": orders.exclude(status__in=FINISHED).count(),
        "orders_week": orders.filter(created_at__gte=week_ago).count(),
        "deals": metrics["confirmed_deals"],
        "conversion": metrics["conversion_percent"],
        "providers": providers.count(),
        "clients": User.objects.filter(role=User.Role.CLIENT).count(),
        "offers_waiting": Offer.objects.filter(status=Offer.Status.REQUESTED).count(),
        "providers_unconfirmed": providers.filter(data_status=Provider.DataStatus.COLLECTED).count(),
        "photos_pending": ProviderPhoto.objects.filter(is_approved=False).count(),
        "reviews_pending": Review.objects.filter(status=Review.Status.PENDING).count(),
        "recent_orders": list(orders.select_related("client", "service_type", "provider").order_by("-created_at")[:8]),
    }
