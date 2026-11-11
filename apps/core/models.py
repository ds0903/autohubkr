from django.db import models


class TimeStampedModel(models.Model):
    """Абстрактна база: час створення та оновлення запису."""

    created_at = models.DateTimeField("створено", auto_now_add=True)
    updated_at = models.DateTimeField("оновлено", auto_now=True)

    class Meta:
        abstract = True


class Notification(TimeStampedModel):
    """Черга сповіщень у Telegram (outbox). Сервіси лише кладуть записи, надсилає їх бот.

    Так бізнес-логіка не залежить від Telegram і легко тестується.
    """

    user = models.ForeignKey(
        "accounts.User", verbose_name="кому", on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField("тип", max_length=32, default="info")
    text = models.TextField("текст")
    payload = models.JSONField("дані для кнопок", default=dict, blank=True)
    dedupe_key = models.CharField("ключ дедуплікації", max_length=120, unique=True, null=True, blank=True)
    sent_at = models.DateTimeField("надіслано", null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("спроб надсилання", default=0)
    error = models.CharField("остання помилка", max_length=255, blank=True)

    class Meta:
        verbose_name = "сповіщення"
        verbose_name_plural = "сповіщення"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.kind} → {self.user_id}"


class City(TimeStampedModel):
    """Місто. Закладено одразу, щоб вихід в інші міста не вимагав переробки (розділ 13 ТЗ)."""

    name = models.CharField("назва", max_length=100, unique=True)
    slug = models.SlugField("slug", max_length=100, unique=True)
    is_active = models.BooleanField("активне", default=True)

    class Meta:
        verbose_name = "місто"
        verbose_name_plural = "міста"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
