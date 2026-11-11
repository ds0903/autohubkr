import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Category(TimeStampedModel):
    """Категорія виконавців: СТО, мийка, шиномонтаж тощо (розділ 4 ТЗ)."""

    class RevenueModel(models.TextChoices):
        COMMISSION = "commission", "Комісія за нових клієнтів"
        SUBSCRIPTION = "subscription", "Підписка / платне просування"
        PARTNER = "partner", "Партнерські умови"

    name = models.CharField("назва", max_length=100, unique=True)
    slug = models.SlugField("slug", max_length=100, unique=True)
    description = models.TextField("опис", blank=True)
    revenue_model = models.CharField(
        "модель доходу",
        max_length=16,
        choices=RevenueModel.choices,
        default=RevenueModel.COMMISSION,
        help_text="Розділ 7.3 ТЗ: різні категорії мають різні моделі доходу.",
    )
    sort_order = models.PositiveSmallIntegerField("порядок", default=100)
    is_active = models.BooleanField("активна", default=True)

    class Meta:
        verbose_name = "категорія"
        verbose_name_plural = "категорії"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class ServiceType(TimeStampedModel):
    """Типова послуга (діагностика ходової, заміна мастила...). Її визначає ШІ за симптомами."""

    category = models.ForeignKey(
        Category, verbose_name="категорія", on_delete=models.PROTECT, related_name="service_types"
    )
    name = models.CharField("назва", max_length=150)
    slug = models.SlugField("slug", max_length=150, unique=True)
    keywords = models.TextField(
        "ключові слова / симптоми",
        blank=True,
        help_text="Через кому. Підказка для ШІ-підбору: стук, вібрація, горить check engine...",
    )
    is_safety_critical = models.BooleanField(
        "критично для безпеки",
        default=False,
        help_text="Гальма, кермо, колеса, подушки безпеки: ШІ радить негайно звернутися в сервіс (розділ 10 ТЗ).",
    )
    is_active = models.BooleanField("активна", default=True)

    class Meta:
        verbose_name = "тип послуги"
        verbose_name_plural = "типи послуг"
        ordering = ["category", "name"]
        constraints = [
            models.UniqueConstraint(fields=["category", "name"], name="uniq_service_type_per_category"),
        ]

    def __str__(self) -> str:
        return self.name


class CarBrand(TimeStampedModel):
    """Марка автомобіля: для спеціалізації виконавців і для гаража."""

    name = models.CharField("назва", max_length=60, unique=True)
    slug = models.SlugField("slug", max_length=60, unique=True)

    class Meta:
        verbose_name = "марка авто"
        verbose_name_plural = "марки авто"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Provider(TimeStampedModel):
    """Виконавець: картка в каталозі (розділ 6.1 ТЗ)."""

    class DataStatus(models.TextChoices):
        COLLECTED = "collected", "Зібрано"
        OWNER_CONFIRMED = "owner_confirmed", "Підтверджено власником"
        VERIFIED = "verified", "Перевірено платформою"

    city = models.ForeignKey(
        "core.City", verbose_name="місто", on_delete=models.PROTECT, related_name="providers"
    )
    categories = models.ManyToManyField(Category, verbose_name="категорії", related_name="providers")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="власник (акаунт)",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_providers",
        help_text="Акаунт виконавця для кабінету та відповідей у боті.",
    )

    name = models.CharField("назва", max_length=200)
    description = models.TextField("опис", blank=True)
    district = models.CharField("район", max_length=100, blank=True)
    address = models.CharField("адреса", max_length=255, blank=True)
    working_hours = models.CharField("графік роботи", max_length=255, blank=True)

    # Контакти приховані до підтвердження запису (розділ 7.2 ТЗ)
    phone = models.CharField("телефон", max_length=64, blank=True)
    telegram_username = models.CharField("Telegram", max_length=64, blank=True)
    website = models.URLField("сайт", blank=True)

    brands = models.ManyToManyField(
        CarBrand,
        verbose_name="спеціалізація за марками",
        blank=True,
        related_name="providers",
        help_text="Порожньо = працює з усіма марками.",
    )

    data_status = models.CharField(
        "статус даних", max_length=16, choices=DataStatus.choices, default=DataStatus.COLLECTED
    )
    source_note = models.CharField("звідки зібрано", max_length=255, blank=True)

    # Рейтинг і лічильники оновлюються сервісами після підтверджених робіт і модерованих відгуків
    rating = models.DecimalField("рейтинг", max_digits=3, decimal_places=2, default=0)
    reviews_count = models.PositiveIntegerField("кількість відгуків", default=0)
    confirmed_deals_count = models.PositiveIntegerField("підтверджених робіт", default=0)
    avg_response_minutes = models.PositiveIntegerField(
        "середній час відповіді, хв", null=True, blank=True
    )

    # Платне просування маркується як реклама (розділ 10 ТЗ)
    promoted_until = models.DateTimeField("просування до", null=True, blank=True)
    is_active = models.BooleanField("показувати в каталозі", default=True)

    # Особиста ссилка/QR виконавця для його власних клієнтів (розділ 7.2 ТЗ): без комісії або символічна
    referral_code = models.CharField("код запрошення", max_length=12, unique=True, null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "виконавець"
        verbose_name_plural = "виконавці"
        ordering = ["-rating", "name"]
        indexes = [models.Index(fields=["city", "is_active"])]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("web:provider", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.referral_code:
            self.referral_code = self._new_referral_code()
        super().save(*args, **kwargs)

    @classmethod
    def _new_referral_code(cls) -> str:
        while True:
            code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")[:8].lower()
            if not cls.objects.filter(referral_code=code).exists():
                return code

    @property
    def is_promoted(self) -> bool:
        return bool(self.promoted_until and self.promoted_until > timezone.now())


class Service(TimeStampedModel):
    """Послуга виконавця з орієнтовною вилкою цін."""

    provider = models.ForeignKey(
        Provider, verbose_name="виконавець", on_delete=models.CASCADE, related_name="services"
    )
    service_type = models.ForeignKey(
        ServiceType,
        verbose_name="тип послуги",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="services",
    )
    name = models.CharField("назва", max_length=200)
    price_min = models.DecimalField("ціна від, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField("ціна до, грн", max_digits=10, decimal_places=2, null=True, blank=True)
    price_note = models.CharField("примітка до ціни", max_length=255, blank=True)
    price_updated_at = models.DateField("ціну актуалізовано", null=True, blank=True)
    is_active = models.BooleanField("активна", default=True)

    class Meta:
        verbose_name = "послуга"
        verbose_name_plural = "послуги"
        ordering = ["provider", "name"]

    def __str__(self) -> str:
        return f"{self.provider}: {self.name}"


class ProviderPhoto(TimeStampedModel):
    """Фото виконаних робіт і зони робіт; публікується після модерації."""

    class Kind(models.TextChoices):
        WORK = "work", "Виконана робота"
        PREMISES = "premises", "Зона робіт"

    provider = models.ForeignKey(
        Provider, verbose_name="виконавець", on_delete=models.CASCADE, related_name="photos"
    )
    kind = models.CharField("тип", max_length=16, choices=Kind.choices, default=Kind.WORK)
    image = models.ImageField("фото", upload_to="providers/%Y/%m/")
    caption = models.CharField("підпис", max_length=255, blank=True)
    is_approved = models.BooleanField("схвалено модератором", default=False)

    class Meta:
        verbose_name = "фото виконавця"
        verbose_name_plural = "фото виконавців"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.provider} ({self.get_kind_display()})"
