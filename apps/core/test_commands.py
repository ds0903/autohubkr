"""Команди керування та запуск бота: те, що виконується на старті проєкту, а не під час звичайних запитів."""
import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.assistant import services as ai
from apps.bot import reminders, runner
from apps.catalog.models import Category, Provider, ServiceType
from apps.core.models import City


def run(*args):
    out = StringIO()
    call_command(*args, stdout=out, stderr=out)
    return out.getvalue()


class SeedCommandsTests(TestCase):
    def test_seed_initial_is_idempotent(self):
        run("seed_initial")
        counts = (Category.objects.count(), ServiceType.objects.count(), City.objects.count())
        self.assertGreater(counts[1], 10)
        run("seed_initial")
        self.assertEqual((Category.objects.count(), ServiceType.objects.count(), City.objects.count()), counts)

    @override_settings(GEMINI_API_KEY="")
    def test_seeded_catalog_drives_the_assistant(self):
        run("seed_initial")
        run("seed_demo")
        city = City.objects.get(slug="kryvyi-rih")
        reply = ai.build_reply("Не гальмує, педаль провалюється", city=city)
        self.assertEqual(reply.service_type.slug, "brakes")
        self.assertIsNotNone(reply.safety_warning)
        reply = ai.build_reply("стукає підвіска спереду на ямах", city=city)
        self.assertEqual(reply.service_type.slug, "diag-chassis")
        self.assertTrue(reply.providers)
        self.assertTrue(all(s.provider.name.startswith("[ДЕМО]") for s in reply.providers))

    def test_seed_demo_requires_initial_data_then_creates_and_clears(self):
        self.assertIn("Спершу виконайте", run("seed_demo"))
        self.assertEqual(Provider.objects.count(), 0)

        run("seed_initial")
        run("seed_demo")
        created = Provider.objects.count()
        self.assertGreaterEqual(created, 8)
        run("seed_demo")
        self.assertEqual(Provider.objects.count(), created, "повторний запуск не дублює")

        real = Provider.objects.create(city=City.objects.get(slug="kryvyi-rih"), name="Справжнє СТО")
        run("seed_demo", "--clear")
        self.assertEqual(list(Provider.objects.all()), [real], "видаляється лише демо, а не реальні записи")

    @override_settings(DEBUG=True)
    def test_seed_dev_users_gives_one_account_per_role_and_is_idempotent(self):
        from apps.accounts.models import User
        run("seed_initial")
        run("seed_demo")
        run("seed_dev_users")
        run("seed_dev_users")
        self.assertEqual(User.objects.count(), 3)
        client, owner, liaison = (User.objects.get(username=n) for n in ("ivan_test", "owner_demo", "liaison"))
        self.assertEqual((client.role, owner.role, liaison.role), ("client", "provider", "liaison"))
        self.assertTrue(liaison.is_staff and liaison.is_superuser)
        self.assertFalse(client.is_staff or owner.is_staff)
        self.assertTrue(client.ai_dialog_consent)
        self.assertFalse(owner.ai_dialog_consent)
        self.assertEqual(Provider.objects.get(name="[ДЕМО] СТО «Північ»").owner, owner)
        self.assertTrue(self.client.login(username="ivan_test", password="S3cure-pass-77"))

    @override_settings(DEBUG=False)
    def test_seed_dev_users_refuses_outside_debug(self):
        from apps.accounts.models import User
        with self.assertRaises(CommandError):
            run("seed_dev_users")
        self.assertEqual(User.objects.count(), 0)


@override_settings(GEMINI_API_KEY="")
class CheckAiCommandTests(SimpleTestCase):
    def test_without_key_it_fails_loudly(self):
        with self.assertRaisesMessage(CommandError, "GEMINI_API_KEY не задано"):
            run("check_ai")

    @override_settings(GEMINI_API_KEY="secret-key-123", GEMINI_MODEL="some-model")
    def test_reports_model_and_answer(self):
        with mock.patch.object(ai, "_call_llm", return_value={"ok": True}):
            out = run("check_ai")
        self.assertIn("some-model", out)
        self.assertIn("'ok': True", out)

    @override_settings(GEMINI_API_KEY="secret-key-123")
    def test_failure_is_reported_without_leaking_the_key(self):
        with mock.patch.object(ai, "_call_llm", side_effect=RuntimeError("404 for key secret-key-123")):
            with self.assertRaises(CommandError) as ctx:
                run("check_ai")
        self.assertIn("Gemini не відповів", str(ctx.exception))
        self.assertNotIn("secret-key-123", str(ctx.exception))


class RunnerTests(SimpleTestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_main_refuses_to_start_without_token(self):
        with self.assertRaises(SystemExit) as ctx:
            asyncio.run(runner.main())
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))

    @override_settings(TELEGRAM_BOT_TOKEN="123456:TEST")
    def test_main_starts_polling_and_always_closes_the_session(self):
        bot = mock.MagicMock()
        bot.get_me = mock.AsyncMock(return_value=SimpleNamespace(username="autohub_test_bot"))
        bot.session.close = mock.AsyncMock()
        dispatcher = mock.MagicMock()
        dispatcher.start_polling = mock.AsyncMock(side_effect=RuntimeError("network down"))
        with mock.patch.object(runner, "Bot", return_value=bot), mock.patch.object(runner, "Dispatcher", return_value=dispatcher), \
                mock.patch.object(runner, "deliver_pending", mock.AsyncMock(return_value=0)), \
                mock.patch.object(reminders, "schedule_visit_reminders"), \
                mock.patch.object(reminders, "schedule_maintenance_reminders"):
            with self.assertRaises(RuntimeError):
                asyncio.run(runner.main())
        dispatcher.include_router.assert_called_once()
        dispatcher.start_polling.assert_awaited_once_with(bot)
        bot.session.close.assert_awaited_once()

    def test_background_loop_survives_errors_and_stops_on_cancel(self):
        calls = []

        async def task():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

        async def scenario():
            sleeper = mock.AsyncMock(side_effect=[None, asyncio.CancelledError()])
            with mock.patch.object(runner.asyncio, "sleep", sleeper):
                await runner._loop("test", 1, task)

        with self.assertLogs("apps.bot.runner", "ERROR"):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(scenario())
        self.assertEqual(len(calls), 2, "після помилки цикл продовжив роботу")

    def test_runbot_command_runs_main_and_handles_ctrl_c(self):
        # basicConfig підміняємо, щоб команда не лишила глобальний обробник логів на решту тестів
        with mock.patch("logging.basicConfig"):
            with mock.patch.object(runner, "main", mock.AsyncMock(return_value=None)) as main:
                run("runbot")
            main.assert_awaited_once()
            with mock.patch.object(runner, "main", mock.AsyncMock(side_effect=KeyboardInterrupt)):
                self.assertIn("Бот зупинено", run("runbot"))
