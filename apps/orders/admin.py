from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone

from apps.core.admin_cards import CardListAdminMixin, price_range, short_dt

from . import services
from .models import BypassReport, Offer, Order, OrderPhoto, OrderStatusLog
from .services import OrderError

ORDER_STATUS_TONE = {
    Order.Status.NEW: "road",
    Order.Status.OFFERED: "warn",
    Order.Status.BOOKED: "ink",
    Order.Status.VISITED: "ink",
    Order.Status.ESTIMATE: "ink",
    Order.Status.DONE: "ok",
    Order.Status.REVIEWED: "ok",
    Order.Status.CANCELLED: "muted",
}
OFFER_STATUS_TONE = {
    Offer.Status.REQUESTED: "warn",
    Offer.Status.PROPOSED: "road",
    Offer.Status.ACCEPTED: "ok",
    Offer.Status.DECLINED: "muted",
    Offer.Status.REJECTED: "muted",
}


def person(user) -> str:
    """Людське ім'я для картки: ім'я, інакше логін."""
    if user is None:
        return ""
    return user.get_full_name() or user.get_username()


def _respond_if_needed(offer: Offer, user) -> None:
    """Якщо зв'язковий виставив статус пропозиції вручну, фіксуємо час відповіді та рух заявки."""
    if offer.status in {Offer.Status.PROPOSED, Offer.Status.DECLINED} and not offer.responded_at:
        services.provider_respond(
            offer,
            accept=offer.status == Offer.Status.PROPOSED,
            price_min=offer.price_min,
            price_max=offer.price_max,
            term_note=offer.term_note,
            suggested_time=offer.suggested_time,
            comment=offer.comment,
            by=user,
        )


OFFER_FIELDS = ("status", "price_min", "price_max", "term_note", "suggested_time", "comment")


def _save_offer(offer: Offer, user) -> Offer:
    """Нова пропозиція з адмінки проходить через сервіс, щоб виконавець отримав запит у Telegram."""
    if not offer._state.adding:
        offer.save()
        _respond_if_needed(offer, user)
        return offer
    draft = {f: getattr(offer, f) for f in OFFER_FIELDS}
    saved = services.request_offer(offer.order, offer.provider, by=user)
    for field, value in draft.items():
        setattr(saved, field, value)
    saved.save()
    _respond_if_needed(saved, user)
    # адмінка після save_model користується початковим об'єктом (redirect, журнал дій), тож синхронізуємо його
    offer.pk, offer._state.adding, offer._state.db = saved.pk, False, saved._state.db
    for field in (*OFFER_FIELDS, "responded_at", "created_at", "updated_at"):
        setattr(offer, field, getattr(saved, field))
    return saved


class OfferInline(admin.TabularInline):
    model = Offer
    extra = 0
    fields = ("provider", "status", "price_min", "price_max", "term_note", "suggested_time", "comment", "responded_at")
    readonly_fields = ("responded_at",)
    raw_id_fields = ("provider",)


class StatusLogInline(admin.TabularInline):
    model = OrderStatusLog
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "from_status", "to_status", "changed_by", "note")

    def has_add_permission(self, request, obj=None):
        return False


class BypassInline(admin.TabularInline):
    model = BypassReport
    extra = 0
    fields = ("side", "reason", "note")


class PhotoInline(admin.TabularInline):
    model = OrderPhoto
    extra = 0


@admin.register(Order)
class OrderAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("id", "created_at", "client", "status", "provider", "liaison", "service_type", "source", "is_deal")
    list_filter = ("status", "city", "source", "lead_origin", "category", "liaison")
    list_select_related = ("client", "city", "provider", "liaison", "service_type", "category", "vehicle__brand")
    search_fields = ("id", "description", "client__username", "client__phone", "provider__name")
    add_label = "Нова заявка"
    card_tab_filter = "status"
    card_bar_filters = ("city", "liaison")
    card_head_links = (("Звіт пілота", "admin:orders_order_report"),)
    card_empty_text = "Заявки з'являться, коли клієнти надішлють їх із сайту чи бота, або додайте першу вручну."
    raw_id_fields = ("client", "vehicle", "provider")
    readonly_fields = ("status", "visit_code", "visit_confirmed_at", "provider_confirmed_at", "client_confirmed_at",
                       "estimate_approved_at")
    inlines = [OfferInline, BypassInline, PhotoInline, StatusLogInline]
    actions = [
        "assign_to_me",
        "mark_visited",
        "approve_estimate_action",
        "confirm_by_provider",
        "confirm_by_client",
        "cancel_selected",
    ]
    fieldsets = (
        ("Заявка", {"fields": ("client", "city", "vehicle", "category", "service_type", "description", "preferred_time",
                               "source", "lead_origin")}),
        ("Хід угоди", {"fields": ("status", "liaison", "provider", "price_min", "price_max", "term_note")}),
        ("Запис і візит", {"fields": ("scheduled_at", "visit_code", "visit_confirmed_at")}),
        ("Кошторис", {"fields": ("estimate_text", "estimate_amount", "estimate_approved_at")}),
        ("Виконання", {"fields": ("final_amount", "work_order_photo", "warranty_days", "provider_confirmed_at",
                                  "client_confirmed_at", "cancel_reason")}),
    )

    @admin.display(boolean=True, description="Угода")
    def is_deal(self, obj):
        return obj.is_confirmed_deal

    def card_title(self, obj):
        return f"Заявка №{obj.pk}"

    def card_subtitle(self, obj):
        what = obj.service_type.name if obj.service_type_id else (obj.category.name if obj.category_id else "")
        car = obj.vehicle.display_name if obj.vehicle_id else ""
        return " · ".join(part for part in (what, car) if part)

    def card_text(self, obj):
        return obj.description

    def card_badges(self, obj):
        badges = [(obj.get_status_display(), ORDER_STATUS_TONE.get(obj.status, "muted"))]
        if obj.is_confirmed_deal:
            badges.append(("Угода", "signal"))
        return badges

    def card_meta(self, obj):
        return [
            ("Клієнт", person(obj.client)),
            ("Виконавець", obj.provider.name if obj.provider_id else None),
            ("Відповідальний", person(obj.liaison)),
            ("Місто", obj.city.name),
            ("Канал", obj.get_source_display()),
            ("Створено", short_dt(obj.created_at)),
            ("Візит", short_dt(obj.scheduled_at) if obj.scheduled_at else None),
        ]

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instances:
            if isinstance(obj, Offer):
                _save_offer(obj, request.user)
                continue
            if isinstance(obj, BypassReport) and not obj.reported_by_id:
                obj.reported_by = request.user
            obj.save()
        formset.save_m2m()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            services.log_manual_creation(obj, request.user)

    def _each(self, request, queryset, fn, ok_text: str):
        done = 0
        for order in queryset:
            try:
                fn(order)
                done += 1
            except OrderError as exc:
                self.message_user(request, f"Заявка №{order.pk}: {exc}", messages.ERROR)
        if done:
            self.message_user(request, f"{ok_text}: {done}", messages.SUCCESS)

    @admin.action(description="Призначити мене відповідальним")
    def assign_to_me(self, request, queryset):
        queryset.update(liaison=request.user)
        self.message_user(request, f"Призначено: {queryset.count()}", messages.SUCCESS)

    @admin.action(description="Візит відбувся (підтверджено зв'язковим)")
    def mark_visited(self, request, queryset):
        def fn(o):
            o.visit_confirmed_at = o.visit_confirmed_at or timezone.now()
            o.save(update_fields=["visit_confirmed_at", "updated_at"])
            services.change_status(o, Order.Status.VISITED, by=request.user, note="Візит підтвердив зв'язковий")

        self._each(request, queryset, fn, "Візит зафіксовано")

    @admin.action(description="Клієнт погодив кошторис")
    def approve_estimate_action(self, request, queryset):
        self._each(request, queryset, lambda o: services.approve_estimate(o, by=request.user), "Кошторис погоджено")

    @admin.action(description="Виконавець підтвердив виконання")
    def confirm_by_provider(self, request, queryset):
        self._each(
            request, queryset,
            lambda o: services.confirm_work(o, side=BypassReport.Side.PROVIDER, by=request.user,
                                            final_amount=o.final_amount, warranty_days=o.warranty_days),
            "Підтвердження виконавця",
        )

    @admin.action(description="Клієнт підтвердив виконання")
    def confirm_by_client(self, request, queryset):
        self._each(
            request, queryset,
            lambda o: services.confirm_work(o, side=BypassReport.Side.CLIENT, by=request.user),
            "Підтвердження клієнта",
        )

    @admin.action(description="Скасувати заявку")
    def cancel_selected(self, request, queryset):
        self._each(request, queryset, lambda o: services.cancel_order(o, "Скасовано зв'язковим", by=request.user),
                   "Скасовано")

    def get_urls(self):
        custom = [path("report/", self.admin_site.admin_view(self.report_view), name="orders_order_report")]
        return custom + super().get_urls()

    def report_view(self, request):
        metrics = services.pilot_metrics()
        context = dict(
            self.admin_site.each_context(request),
            title="Журнал зв'язкового: метрики пілота",
            metrics=metrics,
            # у порядку життєвого циклу та з підписами замість внутрішніх кодів
            status_rows=[(label, metrics["by_status"][code]) for code, label in Order.Status.choices
                         if code in metrics["by_status"]],
            reason_rows=[(label, metrics["bypass_by_reason"][code]) for code, label in BypassReport.Reason.choices
                         if code in metrics["bypass_by_reason"]],
        )
        return TemplateResponse(request, "admin/orders/report.html", context)

@admin.register(Offer)
class OfferAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("order", "provider", "status", "price_min", "price_max", "response_minutes_display")
    list_filter = ("status",)
    list_select_related = ("order", "provider")
    raw_id_fields = ("order", "provider")
    readonly_fields = ("responded_at",)
    actions = ["choose_offer"]
    add_label = "Нова пропозиція"
    card_tab_filter = "status"

    def card_title(self, obj):
        return f"Заявка №{obj.order_id}"

    def card_subtitle(self, obj):
        return obj.provider.name

    def card_text(self, obj):
        return obj.comment

    def card_badges(self, obj):
        return [(obj.get_status_display(), OFFER_STATUS_TONE.get(obj.status, "muted"))]

    def card_meta(self, obj):
        has_price = obj.price_min is not None or obj.price_max is not None
        return [
            ("Ціна", price_range(obj.price_min, obj.price_max) if has_price else None),
            ("Строк", obj.term_note),
            ("Час запису", obj.suggested_time),
            ("Відповів за", f"{obj.response_minutes} хв" if obj.response_minutes is not None else None),
            ("Створено", short_dt(obj.created_at)),
        ]

    @admin.display(description="Відповідь, хв")
    def response_minutes_display(self, obj):
        return obj.response_minutes

    def save_model(self, request, obj, form, change):
        _save_offer(obj, request.user)

    @admin.action(description="Обрати пропозицію → створити запис")
    def choose_offer(self, request, queryset):
        for offer in queryset:
            try:
                services.book_visit(offer.order, offer, scheduled_at=offer.order.scheduled_at, by=request.user)
                self.message_user(request, f"Заявка №{offer.order_id}: запис створено", messages.SUCCESS)
            except OrderError as exc:
                self.message_user(request, f"Заявка №{offer.order_id}: {exc}", messages.ERROR)


@admin.register(BypassReport)
class BypassReportAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("order", "side", "reason", "created_at")
    list_filter = ("side", "reason")
    list_select_related = ("order",)
    raw_id_fields = ("order",)
    add_label = "Записати випадок"
    card_tab_filter = "side"

    def card_title(self, obj):
        return f"Заявка №{obj.order_id}"

    def card_subtitle(self, obj):
        return obj.get_side_display()

    def card_text(self, obj):
        return obj.note

    def card_badges(self, obj):
        return [(obj.get_reason_display(), "warn")]

    def card_meta(self, obj):
        return [("Створено", short_dt(obj.created_at))]
