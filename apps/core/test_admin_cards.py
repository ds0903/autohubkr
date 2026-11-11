"""Картковий вигляд списків і власні форми адмінки: замість таблиць Jazzmin і select2."""
from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Provider
from apps.core.admin_cards import CardListAdminMixin, money, price_range, short_dt
from apps.core.models import City
from apps.core.testing import make_world
from apps.orders import services
from apps.orders.models import Order
from apps.reviews.models import Review


def card_admins():
    """Усі зареєстровані адмінки з картковим списком."""
    return [(model, model_admin) for model, model_admin in admin.site._registry.items()
            if isinstance(model_admin, CardListAdminMixin)]


def url_of(model, kind, *args):
    return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{kind}", args=args)


class AdminTestCase(TestCase):
    def setUp(self):
        self.w = make_world()
        self.admin = User.objects.create_superuser("liaison", password="pass12345", role=User.Role.LIAISON)
        self.order = services.create_order(client=self.w["client"], city=self.w["city"], description="Стукає спереду",
                                           vehicle=self.w["vehicle"], service_type=self.w["chassis"])
        self.client.force_login(self.admin)


class CardListTests(AdminTestCase):
    def test_every_list_is_cards_not_a_table_or_select2(self):
        self.assertGreaterEqual(len(card_admins()), 15, "картковий вигляд має бути в усіх розділах")
        for model, _model_admin in card_admins():
            resp = self.client.get(url_of(model, "changelist"))
            self.assertEqual(resp.status_code, 200, model)
            html = resp.content.decode()
            self.assertIn("cm-page", html, model)
            self.assertNotIn('id="result_list"', html, model)
            self.assertNotIn("select2", html, model)
            self.assertNotIn("dataTables", html, model)

    def test_every_form_is_custom_without_select2_or_dual_lists(self):
        for model, model_admin in card_admins():
            urls = [url_of(model, "add")] if model_admin.has_add_permission(_FakeRequest(self.admin)) else []
            obj = model.objects.first()
            if obj is not None:
                urls.append(url_of(model, "change", obj.pk))
                urls.append(url_of(model, "delete", obj.pk))
            for url in urls:
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200, url)
                html = resp.content.decode()
                self.assertIn("cm-page--form", html, url)
                for forbidden in ("select2", "selector-available", "admin-autocomplete", "vSelectMultiple"):
                    self.assertNotIn(forbidden, html, f"{url}: {forbidden}")

    def test_card_shows_title_badges_meta_and_actions(self):
        provider = self.w["provider"]
        resp = self.client.get(url_of(Provider, "changelist"))
        self.assertContains(resp, 'class="cm-card')
        self.assertContains(resp, provider.name)
        self.assertContains(resp, "Інгулецький")
        self.assertContains(resp, "Зібрано")  # статус даних як бейдж
        self.assertContains(resp, provider.phone)
        self.assertContains(resp, url_of(Provider, "change", provider.pk))
        self.assertContains(resp, url_of(Provider, "delete", provider.pk))
        self.assertContains(resp, 'class="cm-badge"')

    def test_order_card_and_head_link_to_report(self):
        resp = self.client.get(url_of(Order, "changelist"))
        self.assertContains(resp, f"Заявка №{self.order.pk}")
        self.assertContains(resp, "Стукає спереду")
        self.assertContains(resp, "Діагностика ходової")
        self.assertContains(resp, reverse("admin:orders_order_report"))
        self.assertContains(resp, "Нова заявка")

    def test_status_tabs_filter_the_list(self):
        done = services.create_order(client=self.w["client"], city=self.w["city"], description="Друга")
        Order.objects.filter(pk=done.pk).update(status=Order.Status.DONE)
        resp = self.client.get(url_of(Order, "changelist"))
        tabs = resp.context["card_tabs"]
        self.assertEqual(tabs["key"], "status")
        self.assertContains(resp, 'class="cm-tab is-active"')
        resp = self.client.get(url_of(Order, "changelist") + "?status__exact=done")
        self.assertEqual([card["pk"] for card in resp.context["cards"]], [done.pk])
        self.assertContains(resp, f"Заявка №{done.pk}")
        self.assertNotContains(resp, f"Заявка №{self.order.pk}<")

    def test_filter_panel_uses_selects_for_other_filters_and_keeps_params_in_search(self):
        resp = self.client.get(url_of(Order, "changelist") + "?status__exact=new")
        self.assertContains(resp, 'id="cmFilterForm"')
        self.assertContains(resp, 'name="source__exact"')
        self.assertContains(resp, '<input type="hidden" name="status__exact" value="new">', html=True)
        resp = self.client.get(url_of(Order, "changelist") + "?source__exact=bot")
        self.assertEqual(resp.context["cards"], [])
        self.assertContains(resp, "Нічого не знайдено")

    def test_search_finds_cards(self):
        resp = self.client.get(url_of(Provider, "changelist") + "?q=СТО+Тест")
        self.assertEqual(len(resp.context["cards"]), 1)
        resp = self.client.get(url_of(Provider, "changelist") + "?q=немає-такого")
        self.assertEqual(resp.context["cards"], [])

    def test_empty_list_invites_to_create_first_record(self):
        resp = self.client.get(url_of(Review, "changelist"))
        self.assertContains(resp, "Тут ще нічого немає")
        self.assertContains(resp, "після підтверджених робіт")
        self.assertNotContains(resp, 'href="/admin/reviews/review/add/"')  # відгуки створюються лише системою
        self.assertEqual(resp.context["card_filters"], [], "фільтр без варіантів не показується")

    def test_pagination_links_work(self):
        Provider.objects.bulk_create(
            [Provider(city=self.w["city"], name=f"СТО {i:03d}") for i in range(105)])
        resp = self.client.get(url_of(Provider, "changelist"))
        self.assertContains(resp, 'class="cm-pagination"')
        self.assertContains(resp, 'aria-current="page"')
        self.assertEqual(len(resp.context["cards"]), 100)
        second = self.client.get(url_of(Provider, "changelist") + "?p=2")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.context["cards"]), 6)

    def test_lookup_popup_lets_you_pick_a_card(self):
        resp = self.client.get(url_of(User, "changelist") + "?_popup=1&_to_field=id")
        self.assertContains(resp, f'data-popup-opener="{self.w["client"].pk}"')
        self.assertNotContains(resp, "cm-btn--edit")
        self.assertNotContains(resp, "cm-btn--del")

    def test_card_queries_do_not_grow_with_the_number_of_orders(self):
        url = url_of(Order, "changelist")
        self.client.get(url)  # прогрів
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for i in range(8):
            services.create_order(client=self.w["client"], city=self.w["city"], description=f"Ще {i}",
                                  vehicle=self.w["vehicle"], service_type=self.w["chassis"])
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertLessEqual(len(many), len(few) + 1, "N+1: кожна картка робить свої запити")

    def test_bulk_actions_stay_on_cards_and_delete_selected_is_off(self):
        resp = self.client.get(url_of(Order, "changelist"))
        self.assertContains(resp, 'name="_selected_action"')
        self.assertContains(resp, 'id="cmBulk"')
        actions = [value for value, _label in resp.context["action_form"].fields["action"].choices]
        self.assertIn("assign_to_me", actions)
        self.assertNotIn("delete_selected", actions)
        resp = self.client.post(url_of(Order, "changelist"), {
            "action": "assign_to_me", "_selected_action": [self.order.pk], "index": "0"}, follow=True)
        self.assertContains(resp, "Призначено: 1")
        self.order.refresh_from_db()
        self.assertEqual(self.order.liaison, self.admin)

    def test_lists_without_actions_have_no_bulk_bar(self):
        resp = self.client.get(url_of(City, "changelist"))
        self.assertNotContains(resp, 'id="cmBulk"')
        self.assertNotContains(resp, 'name="_selected_action"')

    def test_user_list_has_no_switch_for_own_account(self):
        resp = self.client.get(url_of(User, "changelist"))
        own = next(card for card in resp.context["cards"] if card["pk"] == self.admin.pk)
        other = next(card for card in resp.context["cards"] if card["pk"] == self.w["client"].pk)
        self.assertEqual(own["toggles"], [])
        self.assertEqual(len(other["toggles"]), 1)


class ToggleTests(AdminTestCase):
    def toggle_url(self, provider, field="is_active"):
        return reverse("admin:catalog_provider_toggle", args=[provider.pk, field])

    def test_post_switches_the_field_and_is_logged(self):
        provider = self.w["provider"]
        resp = self.client.post(self.toggle_url(provider))
        self.assertEqual(resp.json(), {"ok": True, "value": False})
        provider.refresh_from_db()
        self.assertFalse(provider.is_active)
        self.assertEqual(LogEntry.objects.filter(object_id=str(provider.pk)).count(), 1)
        self.assertEqual(self.client.post(self.toggle_url(provider)).json(), {"ok": True, "value": True})

    def test_card_renders_the_state_after_the_switch(self):
        provider = self.w["provider"]
        self.client.post(self.toggle_url(provider))
        resp = self.client.get(url_of(Provider, "changelist"))
        self.assertContains(resp, "cm-state--off")
        self.assertContains(resp, ">Прихований<")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.toggle_url(self.w["provider"])).status_code, 405)

    def test_only_declared_fields_can_be_switched(self):
        self.assertEqual(self.client.post(self.toggle_url(self.w["provider"], "rating")).status_code, 404)
        self.assertEqual(self.client.post(reverse("admin:catalog_provider_toggle", args=[9999, "is_active"])).status_code, 404)

    def test_staff_without_change_permission_cannot_switch(self):
        staff = User.objects.create_user("viewer", password="pass12345", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_provider"))
        self.client.force_login(staff)
        resp = self.client.get(url_of(Provider, "changelist"))
        self.assertNotContains(resp, "cm-state")
        self.assertEqual(self.client.post(self.toggle_url(self.w["provider"])).status_code, 403)
        self.w["provider"].refresh_from_db()
        self.assertTrue(self.w["provider"].is_active)

    def test_anonymous_cannot_switch(self):
        self.client.logout()
        resp = self.client.post(self.toggle_url(self.w["provider"]))
        self.assertEqual(resp.status_code, 302)
        self.w["provider"].refresh_from_db()
        self.assertTrue(self.w["provider"].is_active)


class FormLayoutTests(AdminTestCase):
    def test_fields_group_into_rows_with_wide_texts_and_checkboxes_in_a_line(self):
        model_admin = admin.site._registry[Provider]
        layout = dict(model_admin.cm_layout([("Основне", {"fields": ("name", "city", "district", "description", "is_active")})]))
        rows = layout["Основне"]["fields"]
        self.assertEqual(rows, (("name", "city", "district"), ("description",), ("is_active",)))
        self.assertIn("cm-rows", layout["Основне"]["classes"])

    def test_provider_form_groups_everything_in_named_cards(self):
        resp = self.client.get(url_of(Provider, "change", self.w["provider"].pk))
        for text in ("Основне", "Контакти", "Спеціалізація", "Просування", "Статистика", "Послуги", "Фото виконавців"):
            self.assertContains(resp, text)
        self.assertContains(resp, 'class="cm-fieldset cm-fieldset--collapse')
        self.assertContains(resp, 'class="cm-submit"')
        self.assertContains(resp, 'name="_save"')
        self.assertContains(resp, "cm-btn--del")
        # довідкові поля, тільки для читання, з великої літери
        self.assertContains(resp, "Рейтинг")
        self.assertNotContains(resp, "<label>рейтинг</label>")
        # марки й категорії: галочки, а не пошуковий віджет
        self.assertContains(resp, 'class="cm-checks"')
        self.assertContains(resp, 'type="checkbox" name="brands"')

    def test_selects_have_labels_and_are_enhanced_by_script(self):
        resp = self.client.get(url_of(Order, "add"))
        self.assertContains(resp, 'name="city"')
        self.assertRegex(resp.content.decode(), r"core/admin-ui(\.[0-9a-f]+)?\.js")
        self.assertRegex(resp.content.decode(), r"core/admin-ui(\.[0-9a-f]+)?\.css")

    def test_saving_a_form_shows_errors_in_the_new_markup(self):
        resp = self.client.post(url_of(City, "add"), {"name": "", "slug": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "cm-errors")
        self.assertContains(resp, "Виправте помилки нижче.")
        self.assertContains(resp, "has-error")
        self.assertContains(resp, "cm-field-error")

    def test_create_edit_and_delete_round_trip(self):
        resp = self.client.post(url_of(City, "add"), {"name": "Дніпро", "slug": "dnipro", "is_active": "on"}, follow=True)
        self.assertContains(resp, "Дніпро")
        city = City.objects.get(slug="dnipro")
        confirm = self.client.get(url_of(City, "delete", city.pk))
        self.assertContains(confirm, "Так, видалити")
        self.assertContains(confirm, "cm-fieldset--danger")
        self.client.post(url_of(City, "delete", city.pk), {"post": "yes"})
        self.assertFalse(City.objects.filter(slug="dnipro").exists())

    def test_protected_delete_explains_why_it_is_blocked(self):
        confirm = self.client.get(url_of(City, "delete", self.w["city"].pk))
        self.assertContains(confirm, "Видалення неможливе")
        self.assertNotContains(confirm, "Так, видалити")

    def test_inline_table_keeps_management_form_and_delete_buttons(self):
        resp = self.client.get(url_of(Provider, "change", self.w["provider"].pk))
        self.assertContains(resp, 'name="services-TOTAL_FORMS"')
        self.assertContains(resp, "cm-row-delete")
        self.assertContains(resp, 'id="services-empty"')

    def test_user_form_has_permission_checkboxes_not_a_dual_list(self):
        resp = self.client.get(url_of(User, "change", self.w["client"].pk))
        self.assertContains(resp, 'name="user_permissions"')
        self.assertNotContains(resp, "selector-available")
        self.assertContains(resp, 'class="cm-checks"')

class HelperTests(TestCase):
    def test_money_and_price_range_format_amounts(self):
        self.assertEqual(price_range(Decimal("500"), Decimal("900")), "500–900 грн")
        self.assertEqual(price_range(Decimal("500"), None), "від 500 грн")
        self.assertEqual(price_range(None, Decimal("900")), "до 900 грн")
        self.assertEqual(price_range(Decimal("500"), Decimal("500")), "500 грн")
        self.assertEqual(price_range(None, None), "—")
        self.assertEqual(money(Decimal("99.5")), "99.50 грн")
        self.assertEqual(money(Decimal("1200.00")), "1200 грн")
        self.assertEqual(money(None), "—")

    def test_short_dt_handles_empty_dates_and_datetimes(self):
        self.assertEqual(short_dt(None), "—")
        self.assertEqual(short_dt(date(2026, 11, 2)), "02.11.2026")


class _FakeRequest:
    """Мінімальний запит для перевірки прав у адмінках."""

    def __init__(self, user):
        self.user = user
