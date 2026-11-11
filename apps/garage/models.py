from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

from apps.core.models import TimeStampedModel

# VIN: 17 знаків, літери I, O, Q не використовуються
vin_validator = RegexValidator(
    regex=r"^[A-HJ-NPR-Z0-9]{17}$",
    message="VIN має містити 17 символів: латинські літери (крім I, O, Q) і цифри.",
)

SERVICE_BOOK_DISCLAIMER = "Орієнтовний план за загальними нормативами. Звіряйтесь із сервісною книжкою вашого авто."


class Vehicle(TimeStampedModel):
    """Автомобіль у гаражі клієнта (розділ 6.4 ТЗ). Держномер на MVP не збираємо."""

    class DataSource(models.TextChoices):
        MANUAL = "manual", "Введено вручну"
        VIN_DECODED = "vin_decoded", "Визначено за VIN"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="власник", on_delete=models.CASCADE, related_name="vehicles"
    )
    nickname = models.CharField("назва для себе", max_length=60, blank=True)
    vin = models.CharField("VIN", max_length=17, blank=True, validators=[vin_validator])
    brand = models.ForeignKey(
        "catalog.CarBrand", verbose_name="марка", null=True, blank=True, on_delete=models.SET_NULL, related_name="vehicles"
    )
    model_name = models.CharField("модель", max_length=80, blank=True)
    year = models.PositiveSmallIntegerField("рік випуску", null=True, blank=True)
    engine = models.CharField("двигун", max_length=80, blank=True)
    mileage = models.PositiveIntegerField("пробіг, км", null=True, blank=True)
    mileage_updated_at = models.DateField("пробіг оновлено", null=True, blank=True)
    data_source = models.CharField("джерело даних", max_length=16, choices=DataSource.choices, default=DataSource.MANUAL)
    is_active = models.BooleanField("активне", default=True)

    class Meta:
        verbose_name = "автомобіль"
        verbose_name_plural = "автомобілі"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "vin"], condition=~models.Q(vin=""), name="uniq_vin_per_owner"
            )
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        parts = [str(self.brand) if self.brand_id else "", self.model_name, str(self.year or "")]
        title = " ".join(p for p in parts if p).strip()
        return self.nickname or title or f"Авто №{self.pk}"

    def save(self, *args, **kwargs):
        self.vin = self.vin.strip().upper()
        super().save(*args, **kwargs)


class MaintenanceRule(TimeStampedModel):
    """Загальний норматив ТО: інтервал за пробігом і/або часом. Порожня марка = універсальний."""

    title = models.CharField("робота", max_length=150)
    brand = models.ForeignKey(
        "catalog.CarBrand", verbose_name="марка", null=True, blank=True, on_delete=models.CASCADE, related_name="maintenance_rules",
        help_text="Порожньо = універсальний норматив для всіх марок.",
    )
    interval_km = models.PositiveIntegerField("інтервал, км", null=True, blank=True)
    interval_months = models.PositiveSmallIntegerField("інтервал, міс.", null=True, blank=True)
    note = models.CharField("примітка", max_length=255, blank=True)
    is_active = models.BooleanField("активний", default=True)

    class Meta:
        verbose_name = "норматив ТО"
        verbose_name_plural = "нормативи ТО"
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class Receipt(TimeStampedModel):
    """Фото чека / заказ-наряду, яке ШІ перетворює на запис гаража."""

    class Status(models.TextChoices):
        PENDING = "pending", "Очікує розпізнавання"
        DONE = "done", "Розпізнано"
        FAILED = "failed", "Не вдалося розпізнати"

    vehicle = models.ForeignKey(Vehicle, verbose_name="автомобіль", on_delete=models.CASCADE, related_name="receipts")
    image = models.ImageField("фото чека", upload_to="receipts/%Y/%m/")
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.PENDING)
    parsed = models.JSONField("результат розпізнавання", null=True, blank=True)
    error = models.CharField("помилка", max_length=255, blank=True)

    class Meta:
        verbose_name = "чек"
        verbose_name_plural = "чеки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Чек №{self.pk} ({self.get_status_display()})"


class GarageRecord(TimeStampedModel):
    """Запис гаража: робота або витрата. Одна модель обслуговує і історію, і розхідну частину."""

    class Kind(models.TextChoices):
        SERVICE = "service", "Обслуговування / ремонт"
        PARTS = "parts", "Запчастини"
        FUEL = "fuel", "Пальне"
        INSURANCE = "insurance", "Страховка"
        OTHER = "other", "Інше"

    class Trust(models.TextChoices):
        OWNER_CLAIMED = "owner_claimed", "Зі слів власника"
        PROVIDER_CONFIRMED = "provider_confirmed", "Підтверджено виконавцем"

    vehicle = models.ForeignKey(Vehicle, verbose_name="автомобіль", on_delete=models.CASCADE, related_name="records")
    kind = models.CharField("тип", max_length=16, choices=Kind.choices, default=Kind.SERVICE)
    title = models.CharField("що зроблено / куплено", max_length=200)
    works = models.TextField("роботи", blank=True)
    parts = models.TextField("запчастини", blank=True)
    date = models.DateField("дата")
    mileage = models.PositiveIntegerField("пробіг, км", null=True, blank=True)
    amount = models.DecimalField("сума, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    trust = models.CharField("достовірність", max_length=24, choices=Trust.choices, default=Trust.OWNER_CLAIMED)
    provider = models.ForeignKey(
        "catalog.Provider", verbose_name="виконавець", null=True, blank=True, on_delete=models.SET_NULL, related_name="garage_records"
    )
    order = models.OneToOneField(
        "orders.Order", verbose_name="заявка", null=True, blank=True, on_delete=models.SET_NULL, related_name="garage_record"
    )
    receipt = models.ForeignKey(
        Receipt, verbose_name="чек", null=True, blank=True, on_delete=models.SET_NULL, related_name="records"
    )
    rule = models.ForeignKey(
        MaintenanceRule,
        verbose_name="норматив ТО",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="records",
        help_text="Якщо запис закриває пункт плану ТО.",
    )

    class Meta:
        verbose_name = "запис гаража"
        verbose_name_plural = "записи гаража"
        ordering = ["-date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.vehicle}: {self.title}"
