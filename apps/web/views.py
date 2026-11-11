import io

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts import linking
from apps.assistant import services as assistant
from apps.catalog.models import CarBrand, Category, Provider, ServiceType
from apps.core.models import City
from apps.garage import services as garage_services
from apps.garage.models import GarageRecord, Receipt, Vehicle
from apps.orders import services as order_services
from apps.orders.models import BypassReport, Offer, Order, OrderPhoto
from apps.orders.services import OrderError
from apps.reviews import services as review_services
from apps.reviews.models import Review
from apps.reviews.services import ReviewError

from .forms import (BookForm, OrderForm, ProviderConfirmForm, ReceiptForm, RecordForm, RegisterForm, ReviewForm,
                    VehicleForm)

CONFIRM_SALT = "provider-confirm"
CONFIRM_MAX_AGE = 30 * 24 * 3600


def current_city(request) -> City | None:
    return City.objects.filter(is_active=True).order_by("id").first()


def _rate_limited(request, limit_anon: int = 15, limit_auth: int = 40, window: int = 3600) -> bool:
    """Ліміт звернень до ШІ на користувача (ризик «вартість ШІ» з розділу 15 ТЗ)."""
    if request.user.is_authenticated:
        key, limit = f"ai:u:{request.user.pk}", limit_auth
    else:
        if not request.session.session_key:
            request.session.create()
        key, limit = f"ai:s:{request.session.session_key}", limit_anon
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, window)
    return False


# ---------- Публічні сторінки ----------

def home(request):
    city = current_city(request)
    vehicles = (list(Vehicle.objects.filter(owner=request.user, is_active=True).order_by("-created_at"))
                if request.user.is_authenticated else [])
    ctx = {
        "city": city,
        "categories": Category.objects.filter(is_active=True),
        "top_providers": Provider.objects.filter(is_active=True, city=city)[:6] if city else [],
        "vehicles": vehicles,
        # останнє додане авто підставляємо одразу: людина може лишити його, змінити чи стерти
        "car_default": vehicles[0].display_name if vehicles else "",
    }
    return render(request, "web/home.html", ctx)


def _vehicle_from_request(request) -> Vehicle | None:
    """Авто для підказки: людина вписує його сама («Toyota Camry 2015»); збережені авто гаража лише підказуються.

    Збіг із авто з гаража дає повніші дані (двигун, пробіг); інакше беремо те, що вписано, без запису в базу.
    """
    mine = Vehicle.objects.filter(owner=request.user, is_active=True) if request.user.is_authenticated else Vehicle.objects.none()
    pk = request.POST.get("vehicle", "")
    if pk.isdigit():
        found = mine.filter(pk=int(pk)).first()
        if found:
            return found
    typed = " ".join(request.POST.get("car", "").split())[:80]
    if not typed:
        return None
    for v in mine:
        if v.display_name.lower() == typed.lower():
            return v
    return garage_services.vehicle_from_text(typed)


@require_POST
def ask_ai(request):
    text = request.POST.get("text", "").strip()
    if len(text) < 5:
        return render(request, "web/partials/ai_reply.html", {"error": "Опишіть проблему трохи докладніше."})
    if _rate_limited(request):
        return render(request, "web/partials/ai_reply.html",
                      {"error": "Забагато запитів. Спробуйте пізніше або залиште заявку: зв'язковий допоможе."})
    vehicle = _vehicle_from_request(request)
    history = request.session.get("ai_history", [])
    reply, _ = assistant.ask(
        text, user=request.user if request.user.is_authenticated else None,
        session_key=request.session.session_key or "", vehicle=vehicle, city=current_city(request), history=history,
    )
    history += [{"role": "user", "text": text[:500]}, {"role": "assistant", "text": reply.what_to_do[:500]}]
    request.session["ai_history"] = history[-8:]
    return render(request, "web/partials/ai_reply.html", {"reply": reply, "query": text})


def catalog(request):
    city = current_city(request)
    qs = Provider.objects.filter(is_active=True).prefetch_related("categories", "brands")
    if city:
        qs = qs.filter(city=city)
    category = request.GET.get("category")
    brand = request.GET.get("brand")
    q = request.GET.get("q", "").strip()
    if category:
        qs = qs.filter(categories__slug=category)
    if brand:
        qs = qs.filter(Q(brands__slug=brand) | Q(brands__isnull=True))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(district__icontains=q) | Q(services__name__icontains=q))
    qs = qs.distinct().order_by("-rating", "-confirmed_deals_count", "name")
    return render(request, "web/catalog.html", {
        "providers": qs, "categories": Category.objects.filter(is_active=True),
        "brands": CarBrand.objects.all(), "selected": {"category": category, "brand": brand, "q": q},
    })


def provider_detail(request, pk):
    provider = get_object_or_404(
        Provider.objects.prefetch_related("categories", "brands", "services__service_type"), pk=pk, is_active=True
    )
    return render(request, "web/provider_detail.html", {
        "provider": provider,
        "services": provider.services.filter(is_active=True),
        "photos": provider.photos.filter(is_approved=True),
        "reviews": provider.reviews.filter(status=Review.Status.APPROVED).select_related("client")[:20],
    })


def privacy(request):
    return render(request, "web/privacy.html")


def referral(request, code):
    """Особиста ссилка/QR виконавця: заявки його власних клієнтів не вважаються клієнтами платформи."""
    provider = get_object_or_404(Provider, referral_code=code, is_active=True)
    request.session["ref_provider"] = provider.pk
    return redirect(f"{reverse('web:order_new')}?provider={provider.pk}")


def referral_qr(request, code):
    """QR-код особистої ссилки виконавця (SVG) для друку на візитці / стійці."""
    provider = get_object_or_404(Provider, referral_code=code, is_active=True)
    try:
        import segno
    except ImportError:
        raise Http404("QR недоступний")
    url = request.build_absolute_uri(reverse("web:referral", args=[provider.referral_code]))
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2)
    return HttpResponse(buf.getvalue(), content_type="image/svg+xml")


def provider_confirm(request, token):
    """Власник перевіряє й підтверджує дані своєї картки за одноразовим посиланням (розділ 9 ТЗ)."""
    try:
        pk = signing.loads(token, salt=CONFIRM_SALT, max_age=CONFIRM_MAX_AGE)
    except signing.BadSignature:
        return render(request, "web/provider_confirm.html", {"expired": True}, status=410)
    provider = get_object_or_404(Provider, pk=pk)
    form = ProviderConfirmForm(request.POST or None, instance=provider)
    if request.method == "POST" and form.is_valid():
        provider = form.save(commit=False)
        provider.data_status = Provider.DataStatus.OWNER_CONFIRMED
        provider.save()
        return render(request, "web/provider_confirm.html", {"done": True, "provider": provider})
    return render(request, "web/provider_confirm.html", {"form": form, "provider": provider})


def make_confirm_token(provider: Provider) -> str:
    return signing.dumps(provider.pk, salt=CONFIRM_SALT)


def robots(request):
    body = "\n".join([
        "User-agent: *", "Allow: /", "Disallow: /admin/", "Disallow: /orders/", "Disallow: /garage/",
        "Disallow: /accounts/", "Disallow: /confirm/", f"Sitemap: {request.build_absolute_uri(reverse('web:sitemap'))}", "",
    ])
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


# ---------- Акаунти ----------

def register(request):
    if request.user.is_authenticated:
        return redirect("web:home")
    form = RegisterForm(request.POST or None)
    # куди повернути людину після реєстрації (наприклад, до заявки, яку вона саме збиралась залишити)
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        next_url = ""
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.city = current_city(request)
        user.save()
        login(request, user)
        if next_url:
            messages.success(request, "Ласкаво просимо! Акаунт створено, можете завершити заявку.")
            return redirect(next_url)
        messages.success(request, "Ласкаво просимо! Додайте авто в гараж, щоб ШІ та заявки враховували його дані.")
        return redirect("web:garage")
    return render(request, "registration/register.html", {"form": form, "next": next_url})


@login_required
def profile(request):
    if request.method == "POST":
        request.user.ai_dialog_consent = bool(request.POST.get("ai_consent"))
        request.user.save(update_fields=["ai_dialog_consent"])
        messages.success(request, "Налаштування збережено.")
        return redirect("web:profile")
    # None, якщо бота немає (токена й імені нема): тоді блок про Telegram не показуємо взагалі
    return render(request, "web/profile.html", {"tg": linking.bot_links(request.user)})


# ---------- Заявки ----------

@login_required
def order_new(request):
    city = request.user.city or current_city(request)
    provider = None
    if request.GET.get("provider"):
        provider = Provider.objects.filter(pk=request.GET["provider"], is_active=True).first()
    initial = {}
    if request.GET.get("service"):
        st = ServiceType.objects.filter(slug=request.GET["service"]).first()
        if st:
            initial["service_type"] = st
    if request.GET.get("text"):
        initial["description"] = request.GET["text"][:1000]
    # останнє додане авто підставляємо одразу, щоб не змушувати вибирати єдине авто вручну
    last_vehicle = Vehicle.objects.filter(owner=request.user, is_active=True).order_by("-created_at").first()
    if last_vehicle:
        initial["vehicle"] = last_vehicle
    form = OrderForm(request.POST or None, request.FILES or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        own_client = bool(provider and request.session.get("ref_provider") == provider.pk)
        order = order_services.create_order(
            client=request.user, city=city, description=form.cleaned_data["description"],
            vehicle=form.cleaned_data["vehicle"], service_type=form.cleaned_data["service_type"],
            provider=provider, preferred_time=form.cleaned_data["preferred_time"], source=Order.Source.SITE,
            lead_origin=Order.LeadOrigin.PROVIDER_OWN if own_client else Order.LeadOrigin.PLATFORM,
        )
        for image in form.cleaned_data["photos"]:
            OrderPhoto.objects.create(order=order, image=image)
        messages.success(request, "Заявку створено. Зв'язковий підбере виконавців і зв'яжеться з вами.")
        return redirect("web:order_detail", pk=order.pk)
    return render(request, "web/order_form.html", {"form": form, "provider": provider})


@login_required
def order_list(request):
    return render(request, "web/order_list.html", {
        "orders": Order.objects.filter(client=request.user).select_related("provider", "service_type")
    })


@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("provider", "vehicle", "service_type"), pk=pk, client=request.user)
    return render(request, "web/order_detail.html", {
        "order": order,
        "offers": order.offers.select_related("provider").exclude(status=Offer.Status.REQUESTED),
        "pending_offers": order.offers.filter(status=Offer.Status.REQUESTED).count(),
        "log": order.status_log.select_related("changed_by"),
        "review": getattr(order, "review", None),
        "review_form": ReviewForm(),
        "S": Order.Status,
    })


@login_required
@require_POST
def order_action(request, pk):
    order = get_object_or_404(Order, pk=pk, client=request.user)
    action = request.POST.get("action")
    try:
        if action == "book":
            form = BookForm(request.POST)
            if not form.is_valid():
                raise OrderError("Невірні дані запису")
            offer = get_object_or_404(Offer, pk=form.cleaned_data["offer"], order=order)
            scheduled = form.cleaned_data["scheduled_at"]
            if scheduled and timezone.is_naive(scheduled):
                scheduled = timezone.make_aware(scheduled)
            order_services.book_visit(order, offer, scheduled, by=request.user)
            messages.success(request, "Запис створено. Контакти виконавця відкрито; покажіть йому код візиту.")
        elif action == "approve_estimate":
            order_services.approve_estimate(order, by=request.user)
            messages.success(request, "Кошторис погоджено.")
        elif action == "confirm_work":
            order_services.confirm_work(order, side=BypassReport.Side.CLIENT, by=request.user)
            messages.success(request, "Дякуємо! Ви підтвердили виконання роботи.")
        elif action == "cancel":
            order_services.cancel_order(order, request.POST.get("reason", ""), by=request.user)
            messages.info(request, "Заявку скасовано.")
        elif action == "review":
            form = ReviewForm(request.POST)
            if not form.is_valid():
                raise ReviewError("Вкажіть оцінку від 1 до 5")
            review_services.submit_review(order, form.cleaned_data["rating"], form.cleaned_data["comment"])
            messages.success(request, "Відгук надіслано на модерацію.")
        else:
            raise OrderError("Невідома дія")
    except (OrderError, ReviewError) as exc:
        messages.error(request, str(exc))
    return redirect("web:order_detail", pk=order.pk)


# ---------- Гараж ----------

@login_required
def garage(request):
    form = VehicleForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        vehicle = form.save(commit=False)
        vehicle.owner = request.user
        vehicle.mileage_updated_at = timezone.localdate() if vehicle.mileage else None
        vehicle.save()
        garage_services.fill_vehicle_from_vin(vehicle)
        messages.success(request, "Авто додано в гараж.")
        return redirect("web:vehicle", pk=vehicle.pk)
    return render(request, "web/garage.html", {
        "vehicles": Vehicle.objects.filter(owner=request.user, is_active=True), "form": form,
        "brands": CarBrand.objects.all(),
    })


@login_required
def vehicle_detail(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk, owner=request.user)
    garage_services.ensure_default_rules()
    plan, disclaimer = garage_services.maintenance_plan(vehicle)
    return render(request, "web/vehicle.html", {
        "vehicle": vehicle,
        "records": vehicle.records.select_related("provider")[:100],
        "expenses": garage_services.monthly_expenses(vehicle),
        "plan": plan, "plan_disclaimer": disclaimer,
        "record_form": RecordForm(initial={"date": timezone.localdate()}),
        "receipt_form": ReceiptForm(),
        "receipts": vehicle.receipts.all()[:5],
    })


@login_required
@require_POST
def vehicle_record_add(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk, owner=request.user)
    form = RecordForm(request.POST)
    if form.is_valid():
        record = form.save(commit=False)
        record.vehicle = vehicle
        record.save()
        if record.mileage and (vehicle.mileage or 0) < record.mileage:
            vehicle.mileage, vehicle.mileage_updated_at = record.mileage, record.date
            vehicle.save(update_fields=["mileage", "mileage_updated_at", "updated_at"])
        messages.success(request, "Запис додано (позначка: зі слів власника).")
    else:
        messages.error(request, "Перевірте поля запису.")
    return redirect("web:vehicle", pk=vehicle.pk)


@login_required
@require_POST
def vehicle_receipt_add(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk, owner=request.user)
    form = ReceiptForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Завантажте фото чека.")
        return redirect("web:vehicle", pk=vehicle.pk)
    if _rate_limited(request, limit_anon=5, limit_auth=20):
        messages.error(request, "Ліміт розпізнавання чеків вичерпано, спробуйте пізніше.")
        return redirect("web:vehicle", pk=vehicle.pk)
    receipt = Receipt.objects.create(vehicle=vehicle, image=form.cleaned_data["image"])
    record = garage_services.process_receipt(receipt)
    if record:
        messages.success(request, f"Чек розпізнано: «{record.title}». Перевірте дані запису.")
    else:
        messages.error(request, "Не вдалося розпізнати чек. Додайте запис вручну.")
    return redirect("web:vehicle", pk=vehicle.pk)
