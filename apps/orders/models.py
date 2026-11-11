import secrets

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Order(TimeStampedModel):
    """Заявка і вся історія угоди (розділ 7.1 ТЗ).

    Угода = подія «роботу виконано і підтверджено обома сторонами».
    """

    class Status(models.TextChoices):
        NEW = "new", "Заявка"
        OFFERED = "offered", "Пропозиція"
        BOOKED = "booked", "Запис"
        VISITED = "visited", "Візит"
        ESTIMATE = "estimate", "Кошторис погоджено"
        DONE = "done", "Роботу виконано"
        REVIEWED = "reviewed", "Відгук залишено"
        CANCELLED = "cancelled", "Скасовано"

    class Source(models.TextChoices):
        SITE = "site", "Сайт"
        BOT = "bot", "Telegram-бот"
        LIAISON = "liaison", "Зв'язковий"

    class LeadOrigin(models.TextChoices):
        PLATFORM = "platform", "Новий клієнт з платформи"
        PROVIDER_OWN = "provider_own", "Клієнт виконавця (за посиланням / QR)"

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="клієнт",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    city = models.ForeignKey("core.City", verbose_name="місто", on_delete=models.PROTECT, related_name="orders")
    vehicle = models.ForeignKey(
        "garage.Vehicle",
        verbose_name="автомобіль",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    category = models.ForeignKey(
        "catalog.Category", verbose_name="категорія", null=True, blank=True, on_delete=models.SET_NULL
    )
    service_type = models.ForeignKey(
        "catalog.ServiceType", verbose_name="тип послуги", null=True, blank=True, on_delete=models.SET_NULL
    )

    description = models.TextField("опис проблеми / потреби")
    preferred_time = models.CharField("бажаний час", max_length=120, blank=True)
    source = models.CharField("канал", max_length=16, choices=Source.choices, default=Source.SITE)
    lead_origin = models.CharField(
        "походження клієнта", max_length=16, choices=LeadOrigin.choices, default=LeadOrigin.PLATFORM
    )

    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    liaison = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="відповідальний зв'язковий",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_orders",
    )
    provider = models.ForeignKey(
        "catalog.Provider",
        verbose_name="обраний виконавець",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )

    # Узгоджені умови
    price_min = models.DecimalField("вилка ціни від, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField("вилка ціни до, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    term_note = models.CharField("строк виконання", max_length=120, blank=True)

    # Запис і візит
    scheduled_at = models.DateTimeField("дата і час візиту", null=True, blank=True)
    visit_code = models.CharField("код підтвердження візиту", max_length=6, blank=True)
    visit_confirmed_at = models.DateTimeField("візит підтверджено", null=True, blank=True)

    # Кошторис
    estimate_text = models.TextField("перелік робіт (кошторис)", blank=True)
    estimate_amount = models.DecimalField("сума кошторису, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    estimate_approved_at = models.DateTimeField("кошторис погоджено", null=True, blank=True)

    # Виконання
    final_amount = models.DecimalField("фактична сума, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    work_order_photo = models.ImageField("фото заказ-наряду", upload_to="work_orders/%Y/%m/", null=True, blank=True)
    warranty_days = models.PositiveIntegerField("гарантія, днів", null=True, blank=True)
    provider_confirmed_at = models.DateTimeField("виконавець підтвердив роботу", null=True, blank=True)
    client_confirmed_at = models.DateTimeField("клієнт підтвердив роботу", null=True, blank=True)
    cancel_reason = models.CharField("причина скасування", max_length=255, blank=True)

    class Meta:
        verbose_name = "заявка"
        verbose_name_plural = "заявки"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["city", "status"])]

    def __str__(self) -> str:
        return f"Заявка №{self.pk}: {self.description[:40]}"

    @property
    def is_confirmed_deal(self) -> bool:
        """Угода: роботу підтвердили обидві сторони."""
        return bool(self.provider_confirmed_at and self.client_confirmed_at)

    @property
    def contacts_visible(self) -> bool:
        """Контакти виконавця відкриваються клієнту після запису (розділ 7.2 ТЗ)."""
        return self.provider_id is not None and self.status in {
            self.Status.BOOKED,
            self.Status.VISITED,
            self.Status.ESTIMATE,
            self.Status.DONE,
            self.Status.REVIEWED,
        }

    @staticmethod
    def new_visit_code() -> str:
        return f"{secrets.randbelow(10**6):06d}"


class OrderPhoto(TimeStampedModel):
    order = models.ForeignKey(Order, verbose_name="заявка", on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField("фото", upload_to="orders/%Y/%m/")

    class Meta:
        verbose_name = "фото до заявки"
        verbose_name_plural = "фото до заявок"

    def __str__(self) -> str:
        return f"Фото до заявки №{self.order_id}"


class Offer(TimeStampedModel):
    """Пропозиція виконавця по заявці: вилка ціни та строк. Час відповіді вимірюється для метрик."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Очікує відповіді"
        PROPOSED = "proposed", "Виконавець запропонував умови"
        ACCEPTED = "accepted", "Обрано клієнтом"
        DECLINED = "declined", "Виконавець відмовився"
        REJECTED = "rejected", "Клієнт не обрав"

    order = models.ForeignKey(Order, verbose_name="заявка", on_delete=models.CASCADE, related_name="offers")
    provider = models.ForeignKey(
        "catalog.Provider", verbose_name="виконавець", on_delete=models.CASCADE, related_name="offers"
    )
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.REQUESTED)
    price_min = models.DecimalField("ціна від, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField("ціна до, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    term_note = models.CharField("строк", max_length=120, blank=True)
    suggested_time = models.CharField("запропонований час запису", max_length=120, blank=True)
    comment = models.TextField("коментар", blank=True)
    responded_at = models.DateTimeField("час відповіді", null=True, blank=True)

    class Meta:
        verbose_name = "пропозиція"
        verbose_name_plural = "пропозиції"
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["order", "provider"], name="uniq_offer_per_provider")]

    def __str__(self) -> str:
        return f"{self.provider} → заявка №{self.order_id}"

    @property
    def response_minutes(self) -> int | None:
        if not self.responded_at:
            return None
        return int((self.responded_at - self.created_at).total_seconds() // 60)


class OrderStatusLog(models.Model):
    """Журнал статусів: по кожному видно відповідального і час (розділ 6.2 ТЗ)."""

    order = models.ForeignKey(Order, verbose_name="заявка", on_delete=models.CASCADE, related_name="status_log")
    from_status = models.CharField("зі статусу", max_length=16, blank=True)
    to_status = models.CharField("у статус", max_length=16)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="хто змінив",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    note = models.CharField("примітка", max_length=255, blank=True)
    created_at = models.DateTimeField("час", auto_now_add=True)

    class Meta:
        verbose_name = "запис журналу статусів"
        verbose_name_plural = "журнал статусів"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"№{self.order_id}: {self.from_status or '—'} → {self.to_status}"

    @property
    def to_status_display(self) -> str:
        return dict(Order.Status.choices).get(self.to_status, self.to_status)


class BypassReport(TimeStampedModel):
    """Журнал зв'язкового: чому сторона просить прямий контакт / обхід платформи (розділ 8 ТЗ)."""

    class Side(models.TextChoices):
        CLIENT = "client", "Клієнт"
        PROVIDER = "provider", "Виконавець"

    class Reason(models.TextChoices):
        PRICE = "price", "Хоче дешевше / без комісії"
        SPEED = "speed", "Швидше домовитись напряму"
        TRUST = "trust", "Довіра / знайомі"
        HABIT = "habit", "Звичка, постійний клієнт"
        OTHER = "other", "Інше"

    order = models.ForeignKey(Order, verbose_name="заявка", on_delete=models.CASCADE, related_name="bypass_reports")
    side = models.CharField("хто", max_length=16, choices=Side.choices)
    reason = models.CharField("причина", max_length=16, choices=Reason.choices)
    note = models.TextField("коментар", blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="хто записав", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "випадок обходу"
        verbose_name_plural = "випадки обходу платформи"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"№{self.order_id}: {self.get_side_display()} — {self.get_reason_display()}"
