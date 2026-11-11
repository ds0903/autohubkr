from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    """Діалог з ШІ-помічником. Зберігається лише зі згодою користувача (розділ 10 ТЗ)."""

    class Channel(models.TextChoices):
        SITE = "site", "Сайт"
        BOT = "bot", "Telegram-бот"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="користувач", null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations"
    )
    session_key = models.CharField("ключ сесії", max_length=64, blank=True, db_index=True)
    channel = models.CharField("канал", max_length=8, choices=Channel.choices, default=Channel.SITE)
    vehicle = models.ForeignKey(
        "garage.Vehicle", verbose_name="автомобіль", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    city = models.ForeignKey("core.City", verbose_name="місто", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    store_content = models.BooleanField(
        "зберігати тексти", default=False, help_text="Якщо вимкнено, тексти повідомлень не записуються."
    )

    class Meta:
        verbose_name = "діалог з ШІ"
        verbose_name_plural = "діалоги з ШІ"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Діалог №{self.pk}"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Користувач"
        ASSISTANT = "assistant", "Помічник"

    conversation = models.ForeignKey(Conversation, verbose_name="діалог", on_delete=models.CASCADE, related_name="messages")
    role = models.CharField("роль", max_length=12, choices=Role.choices)
    content = models.TextField("текст", blank=True)
    meta = models.JSONField("службові дані", default=dict, blank=True)
    created_at = models.DateTimeField("час", auto_now_add=True)

    class Meta:
        verbose_name = "повідомлення"
        verbose_name_plural = "повідомлення"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.content[:40]}"
