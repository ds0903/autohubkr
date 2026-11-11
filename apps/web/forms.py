from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import CarBrand, Provider, ServiceType
from apps.garage.models import GarageRecord, Vehicle
from apps.orders.models import Order


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(label="Ім'я", max_length=60)
    phone = forms.CharField(label="Телефон", max_length=32, required=False)
    consent = forms.BooleanField(
        label="Погоджуюсь на обробку персональних даних (див. політику конфіденційності)",
        error_messages={"required": "Без згоди на обробку даних реєстрація неможлива."},
    )
    # за замовчуванням увімкнено, але видно й знімається одним кліком; згода на обробку даних вище, навпаки, порожня
    ai_consent = forms.BooleanField(
        label="Дозволяю зберігати мої діалоги з ШІ-помічником для покращення підбору",
        required=False, initial=True,
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "phone")
        labels = {"username": "Логін"}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"]
        user.phone = self.cleaned_data["phone"]
        user.role = User.Role.CLIENT
        user.personal_data_consent_at = timezone.now()
        user.ai_dialog_consent = self.cleaned_data["ai_consent"]
        if commit:
            user.save()
        return user


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data if item]
        return [single(data, initial)] if data else []


class OrderForm(forms.ModelForm):
    photos = MultipleImageField(label="Фото (до 4)", required=False)

    class Meta:
        model = Order
        fields = ("vehicle", "service_type", "description", "preferred_time")
        labels = {
            "vehicle": "Автомобіль",
            "service_type": "Тип послуги (якщо знаєте)",
            "description": "Опишіть проблему або потребу",
            "preferred_time": "Коли зручно",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 5}),
                   "preferred_time": forms.TextInput(attrs={"placeholder": "наприклад: завтра після 16:00"})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vehicle"].queryset = Vehicle.objects.filter(owner=user, is_active=True) if user else Vehicle.objects.none()
        self.fields["vehicle"].required = False
        self.fields["service_type"].queryset = ServiceType.objects.filter(is_active=True)
        self.fields["service_type"].required = False

    def clean_photos(self):
        photos = self.cleaned_data.get("photos") or []
        if len(photos) > 4:
            raise forms.ValidationError("Можна додати не більше 4 фото.")
        return photos


class VehicleForm(forms.ModelForm):
    # Марку вписують самі; список марок лише підказує (datalist). Невідома марка не губиться: іде на початок моделі.
    brand_name = forms.CharField(
        label="Марка", max_length=40, required=False,
        widget=forms.TextInput(attrs={"list": "brand-list", "autocomplete": "off", "placeholder": "наприклад, Toyota"}),
    )
    field_order = ["nickname", "vin", "brand_name", "model_name", "year", "engine", "mileage"]

    class Meta:
        model = Vehicle
        fields = ("nickname", "vin", "model_name", "year", "engine", "mileage")
        labels = {"nickname": "Назва для себе", "vin": "VIN (17 знаків, необов'язково)",
                  "model_name": "Модель", "year": "Рік", "engine": "Двигун", "mileage": "Пробіг, км"}

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        vehicle = super().save(commit=False)
        typed = self.cleaned_data.get("brand_name", "").strip()
        if typed:
            brand = CarBrand.objects.filter(name__iexact=typed).first()
            if brand:
                vehicle.brand = brand
            else:
                vehicle.model_name = f"{typed} {vehicle.model_name}".strip()[:80]
        if commit:
            vehicle.save()
        return vehicle

    def clean_vin(self):
        vin = self.cleaned_data["vin"].strip().upper()
        if vin and self.owner and Vehicle.objects.filter(owner=self.owner, vin=vin).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Авто з таким VIN уже є у вашому гаражі.")
        return vin


class RecordForm(forms.ModelForm):
    class Meta:
        model = GarageRecord
        fields = ("kind", "title", "date", "mileage", "amount", "works", "parts", "rule")
        labels = {"kind": "Тип", "title": "Що зроблено / куплено", "date": "Дата", "mileage": "Пробіг, км",
                  "amount": "Сума, грн", "works": "Роботи", "parts": "Запчастини", "rule": "Закриває пункт плану ТО"}
        # type="date" приймає лише рррр-мм-дд; локалізований формат дає порожнє поле
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
                   "works": forms.Textarea(attrs={"rows": 2}),
                   "parts": forms.Textarea(attrs={"rows": 2})}


class ReceiptForm(forms.Form):
    image = forms.ImageField(label="Фото чека або заказ-наряду")


class ReviewForm(forms.Form):
    rating = forms.IntegerField(label="Оцінка (1–5)", min_value=1, max_value=5)
    comment = forms.CharField(label="Коментар", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ProviderConfirmForm(forms.ModelForm):
    """Власник підтверджує дані своєї картки (розділ 9 ТЗ)."""

    class Meta:
        model = Provider
        fields = ("phone", "telegram_username", "address", "district", "working_hours", "description")
        labels = {"phone": "Телефон", "telegram_username": "Telegram", "address": "Адреса", "district": "Район",
                  "working_hours": "Графік роботи", "description": "Опис"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class BookForm(forms.Form):
    offer = forms.IntegerField(widget=forms.HiddenInput)
    scheduled_at = forms.DateTimeField(label="Дата і час візиту", required=False,
                                       widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                       input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"])
