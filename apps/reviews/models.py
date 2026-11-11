from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Review(TimeStampedModel):
    """Відгук — лише за підтвердженою роботою і лише після модерації (розділ 6.1 ТЗ)."""

    class Status(models.TextChoices):
        PENDING = "pending", "На модерації"
        APPROVED = "approved", "Опубліковано"
        REJECTED = "rejected", "Відхилено"

    order = models.OneToOneField(
        "orders.Order", verbose_name="заявка", on_delete=models.CASCADE, related_name="review"
    )
    provider = models.ForeignKey(
        "catalog.Provider", verbose_name="виконавець", on_delete=models.CASCADE, related_name="reviews"
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="клієнт", on_delete=models.CASCADE, related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(
        "оцінка", validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField("коментар", blank=True)
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    reject_reason = models.CharField("причина відхилення", max_length=255, blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="модератор", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    moderated_at = models.DateTimeField("час модерації", null=True, blank=True)

    class Meta:
        verbose_name = "відгук"
        verbose_name_plural = "відгуки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.provider}: {self.rating}/5"
