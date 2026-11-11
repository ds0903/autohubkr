"""Картковий вигляд списків адмінки та розкладка форм у рядки.

Замість таблиць Django/Jazzmin список показується картками: назва, бейджі стану, короткі «чіпи» з даними,
перемикачі (активний/приховано) і кнопки. Фільтри діляться на вкладки (головний статус), випадні списки
в рядку та панель «Фільтри». Масові дії (`actions`) лишаються: картки отримують галочку вибору.

Як користуватися:

    class ThingAdmin(CardListAdminMixin, admin.ModelAdmin):
        card_tab_filter = "status"
        card_toggles = (("is_active", ("Активна", "Прихована")),)

        def card_title(self, obj): ...
        def card_badges(self, obj): return [("Нова", "road")]
        def card_meta(self, obj): return [("Місто", obj.city)]
"""
from datetime import datetime
from urllib.parse import parse_qs

from django import forms
from django.contrib.admin.views.main import PAGE_VAR
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.urls import path, reverse
from django.utils import formats, timezone

# Тони бейджів: ok (зелений), warn (бурштиновий), danger (червоний), road (синій), ink (темний),
# signal (жовтий), muted (сірий).
TONES = ("ok", "warn", "danger", "road", "ink", "signal", "muted")


def checkbox_list() -> forms.CheckboxSelectMultiple:
    """Галочки в прокручуваній сітці замість двох списків із перенесенням або select2."""
    return forms.CheckboxSelectMultiple(attrs={"class": "cm-checks"})


def short_dt(value, with_time: bool = True) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime) and timezone.is_aware(value):
        value = timezone.localtime(value)
    return formats.date_format(value, "d.m.Y H:i" if with_time and isinstance(value, datetime) else "d.m.Y")


def _fmt_amount(value) -> str:
    return f"{value:.0f}" if value == int(value) else f"{value:.2f}"


def money(value) -> str:
    """Сума: «500 грн» або тире, якщо суми немає."""
    return "—" if value is None else f"{_fmt_amount(value)} грн"


def price_range(low, high) -> str:
    """Вилка цін: «500–900 грн», «від 500 грн», «до 900 грн» або тире."""
    if low is not None and high is not None and low != high:
        return f"{_fmt_amount(low)}–{_fmt_amount(high)} грн"
    if low is not None and high is None:
        return f"від {_fmt_amount(low)} грн"
    if low is None and high is not None:
        return f"до {_fmt_amount(high)} грн"
    return money(low if low is not None else high)


class CardListAdminMixin:
    change_list_template = "admin/card_change_list.html"

    add_label = "Додати"
    card_edit_label = "Відкрити"
    # Перемикачі в картці: ((поле, (текст «так», текст «ні»)), ...). Клік змінює поле без відкриття форми.
    card_toggles = ()
    # Ключ фільтра для рядка вкладок (field_path або parameter_name). Інакше береться перший короткий фільтр.
    card_tab_filter = None
    # Фільтри, що йдуть окремими випадними списками в рядку: назви ключів.
    card_bar_filters = ()
    card_filter_titles = {}
    card_filter_wide = ()
    # Кнопки поруч із «Додати»: ((підпис, ім'я url), ...).
    card_head_links = ()
    card_empty_text = None

    # ---------- розкладка форм: поля групуються в рядки по три, великі тексти й M2M ідуть на всю ширину ----------

    def get_fieldsets(self, request, obj=None):
        return self.cm_layout(super().get_fieldsets(request, obj))

    def cm_layout(self, fieldsets):
        layout = []
        for name, options in fieldsets:
            options = dict(options)
            classes = tuple(options.get("classes", ()))
            if "cm-rows" not in classes:
                classes += ("cm-rows",)
            options["classes"] = classes
            options["fields"] = self._cm_rows(options.get("fields", ()))
            layout.append((name, options))
        return layout

    def _cm_kind(self, name):
        try:
            field = self.model._meta.get_field(name)
        except Exception:
            return "plain"
        if isinstance(field, (models.TextField, models.ManyToManyField)):
            return "wide"
        if isinstance(field, models.BooleanField):
            return "check"
        return "plain"

    def _cm_rows(self, items):
        rows, plain, checks = [], [], []

        def flush(buffer):
            if buffer:
                rows.append(tuple(buffer))
                buffer.clear()

        for item in items:
            if isinstance(item, (list, tuple)):
                flush(plain)
                flush(checks)
                rows.append(tuple(item))
                continue
            kind = self._cm_kind(item)
            if kind == "wide":
                flush(plain)
                flush(checks)
                rows.append((item,))
            elif kind == "check":
                flush(plain)
                checks.append(item)
                if len(checks) == 3:
                    flush(checks)
            else:
                flush(checks)
                plain.append(item)
                if len(plain) == 3:
                    flush(plain)
        flush(plain)
        flush(checks)
        return tuple(rows)

    # ---------- хуки вмісту картки ----------

    def card_title(self, obj):
        return str(obj)

    def card_subtitle(self, obj):
        return ""

    def card_text(self, obj):
        return ""

    def card_image(self, obj):
        """Адреса мініатюри або None; картка з фото вмикається, якщо хоч раз повернули адресу."""
        return None

    def card_badges(self, obj):
        return []

    def card_meta(self, obj):
        return []

    def card_view_url(self, obj):
        getter = getattr(obj, "get_absolute_url", None)
        return getter() if getter else None

    def card_page_title(self, request):
        return None

    # ---------- перемикачі ----------

    def _toggle_url(self, obj, field):
        opts = self.model._meta
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_toggle", args=[obj.pk, field])

    def _toggles(self, request, obj):
        if not self.card_toggles or not self.has_change_permission(request, obj):
            return []
        return [
            {"url": self._toggle_url(obj, field), "on": bool(getattr(obj, field)), "on_label": on, "off_label": off}
            for field, (on, off) in self.card_toggles
        ]

    def get_urls(self):
        urls = super().get_urls()
        if not self.card_toggles:
            return urls
        opts = self.model._meta
        name = f"{opts.app_label}_{opts.model_name}_toggle"
        return [path("<path:object_id>/toggle/<str:field>/", self.admin_site.admin_view(self.toggle_view), name=name)] + urls

    def toggle_view(self, request, object_id, field):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if field not in {name for name, _labels in self.card_toggles}:
            raise Http404
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        value = not getattr(obj, field)
        setattr(obj, field, value)
        update_fields = [field]
        if any(f.name == "updated_at" for f in obj._meta.fields):
            update_fields.append("updated_at")
        obj.save(update_fields=update_fields)
        label = str(obj._meta.get_field(field).verbose_name)
        self.log_change(request, obj, [{"changed": {"fields": [label]}}])
        return JsonResponse({"ok": True, "value": value})

    # ---------- збирання картки та контексту списку ----------

    def _build_card(self, request, obj, to_field=None):
        opts = self.model._meta
        image = self.card_image(obj)
        can_change = self.has_change_permission(request, obj)
        can_delete = self.has_delete_permission(request, obj)
        return {
            "pk": obj.pk,
            "pick": getattr(obj, to_field) if to_field else obj.pk,
            "title": self.card_title(obj),
            "subtitle": self.card_subtitle(obj),
            "text": self.card_text(obj),
            "image": image,
            "badges": self.card_badges(obj),
            "meta": [(label, value) for label, value in self.card_meta(obj) if value not in (None, "")],
            "toggles": self._toggles(request, obj),
            "view_url": self.card_view_url(obj),
            "edit_label": "Переглянути" if not can_change else self.card_edit_label,
            "change_url": reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk]),
            "delete_url": reverse(f"admin:{opts.app_label}_{opts.model_name}_delete", args=[obj.pk]),
            "can_delete": can_delete,
        }

    def _panel_param(self, spec):
        names = [name for name in spec.expected_parameters() if not name.endswith("__isnull")]
        return names[0] if len(names) == 1 else None

    def _panel_options(self, param, choices):
        options = []
        for index, choice in enumerate(choices):
            if index == 0:
                value = ""
            else:
                values = parse_qs(choice["query_string"].lstrip("?")).get(param)
                if not values:
                    continue
                value = values[0]
            options.append({"value": value, "display": choice["display"], "selected": bool(choice["selected"])})
        return options

    def _filter_groups(self, cl):
        groups = []
        for spec in cl.filter_specs:
            key = getattr(spec, "field_path", None) or getattr(spec, "parameter_name", "")
            choices = list(spec.choices(cl))
            param = self._panel_param(spec)
            groups.append({
                "key": key,
                "title": self.card_filter_titles.get(key, spec.title),
                "choices": choices,
                "selected": next((str(c["display"]) for c in choices if c["selected"]), ""),
                "param": param,
                "options": self._panel_options(param, choices) if param else [],
                "wide": key in self.card_filter_wide,
                "active": any(c["selected"] for c in choices[1:]),
            })
        return groups

    def _pages(self, cl):
        paginator = cl.paginator
        if paginator.num_pages <= 1:
            return []
        pages = []
        for number in paginator.get_elided_page_range(cl.page_num, on_each_side=2, on_ends=1):
            if number == paginator.ELLIPSIS:
                pages.append({"label": "…", "url": None, "current": False})
            else:
                pages.append({"label": number, "url": cl.get_query_string({PAGE_VAR: number}), "current": number == cl.page_num})
        return pages

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        data = getattr(response, "context_data", None)
        if data is None or "cl" not in data:
            return response
        cl = data["cl"]
        groups = self._filter_groups(cl)
        bar = [group for key in self.card_bar_filters for group in groups if group["key"] == key]
        rest = [group for group in groups if group not in bar]
        tabs = None
        if self.card_tab_filter:
            tabs = next((g for g in rest if g["key"] == self.card_tab_filter), None)
        else:
            tabs = next((g for g in rest if 2 <= len(g["choices"]) <= 5), None)
        panel = [g for g in rest if g is not tabs and g["param"] and len(g["options"]) > 1]
        to_field = getattr(cl, "to_field", None)
        data.update({
            "card_title": self.card_page_title(request),
            "card_add_label": self.add_label,
            "card_tabs": tabs,
            "card_bar": bar,
            "card_filters": panel,
            "card_filters_active": sum(1 for g in panel if g["active"]),
            "card_hidden_params": [(k, v) for k, v in cl.params.items() if k not in ("q", PAGE_VAR)],
            "card_head_links": [(label, reverse(name)) for label, name in self.card_head_links],
            "card_empty_text": self.card_empty_text,
            "card_pages": self._pages(cl),
            "card_popup": cl.is_popup,
            "cards": [self._build_card(request, obj, to_field) for obj in cl.result_list],
        })
        data["card_has_images"] = any(card["image"] for card in data["cards"])
        return response


__all__ = ["CardListAdminMixin", "checkbox_list", "short_dt", "money", "price_range", "TONES"]
