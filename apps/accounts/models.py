from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Користувач платформи. Чотири ролі з розділу 4 ТЗ."""

    class Role(models.TextChoices):
        CLIENT = "client", "Клієнт"
        PROVIDER = "provider", "Виконавець"
        LIAISON = "liaison", "Зв'язковий"
        ADMIN = "admin", "Адміністратор"

    role = models.CharField("роль", max_length=16, choices=Role.choices, default=Role.CLIENT)
    phone = models.CharField("телефон", max_length=32, blank=True)
    telegram_id = models.BigIntegerField("Telegram ID", null=True, blank=True, unique=True)
    city = models.ForeignKey(
        "core.City",
        verbose_name="місто",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    # Мінімум персональних даних + згоди (розділ 9 ТЗ)
    personal_data_consent_at = models.DateTimeField("згода на обробку даних", null=True, blank=True)
    ai_dialog_consent = models.BooleanField("згода на збереження діалогів з ШІ", default=False)

    class Meta:
        verbose_name = "користувач"
        verbose_name_plural = "користувачі"

    @property
    def is_liaison(self) -> bool:
        return self.role in {self.Role.LIAISON, self.Role.ADMIN}
