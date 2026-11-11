"""Бойова конфігурація: налаштування за DJANGO_DEBUG=0 і файли розгортання (deploy/).

Налаштування перевіряються в окремому процесі: settings.py читається один раз при імпорті,
а змінні середовища тут мають бути повністю під контролем тесту (а не підхоплюватися з локального .env).
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
DEPLOY = BASE / "deploy"
STRONG_KEY = "k" * 50

PROBE = """
import json, os
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
from django.conf import settings as s
print(json.dumps({
    "debug": s.DEBUG,
    "static": s.STORAGES["staticfiles"]["BACKEND"],
    "cache": s.CACHES["default"]["BACKEND"],
    "proxy_header": s.SECURE_PROXY_SSL_HEADER,
    "session_secure": s.SESSION_COOKIE_SECURE,
    "csrf_secure": s.CSRF_COOKIE_SECURE,
    "csrf_origins": s.CSRF_TRUSTED_ORIGINS,
    "hosts": s.ALLOWED_HOSTS,
    "site_url": s.SITE_URL,
}))
"""


def load_settings(**env):
    """Імпортує config.settings в окремому процесі з заданим середовищем; повертає (код, json або stderr)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DB_", "CACHE_", "SITE_"))}
    clean.update({"DB_ENGINE": "sqlite", "DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": STRONG_KEY,
                  "DJANGO_HTTPS": "", "CACHE_BACKEND": "", "DJANGO_CSRF_TRUSTED_ORIGINS": "",
                  "DJANGO_ALLOWED_HOSTS": "127.0.0.1", "SITE_URL": "http://127.0.0.1"})
    clean.update(env)
    done = subprocess.run([sys.executable, "-c", PROBE], cwd=BASE, env=clean, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    if done.returncode:
        return done.returncode, done.stderr
    return 0, json.loads(done.stdout.strip().splitlines()[-1])


class ProductionSettingsTests(SimpleTestCase):
    def test_refuses_to_start_with_default_secret_key(self):
        code, out = load_settings(DJANGO_SECRET_KEY="dev-insecure-change-me")
        self.assertNotEqual(code, 0)
        self.assertIn("ImproperlyConfigured", out)

    def test_refuses_to_start_with_empty_secret_key(self):
        code, out = load_settings(DJANGO_SECRET_KEY="")
        self.assertNotEqual(code, 0)
        self.assertIn("ImproperlyConfigured", out)

    def test_refuses_to_start_with_short_secret_key(self):
        code, out = load_settings(DJANGO_SECRET_KEY="short-key")
        self.assertNotEqual(code, 0)
        self.assertIn("ImproperlyConfigured", out)

    def test_development_may_use_default_key(self):
        code, out = load_settings(DJANGO_DEBUG="1", DJANGO_SECRET_KEY="dev-insecure-change-me")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["debug"])

    def test_production_uses_hashed_static_files(self):
        code, out = load_settings()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["static"], "apps.core.storage.TolerantManifestStaticFilesStorage")

    def test_http_deployment_keeps_cookies_usable(self):
        """Без https (доступ за IP) Secure-cookie зламали б вхід: браузер не надсилає їх по http."""
        code, out = load_settings()
        self.assertEqual(code, 0, out)
        self.assertFalse(out["session_secure"])
        self.assertFalse(out["csrf_secure"])
        self.assertIsNone(out["proxy_header"])

    def test_https_flag_enables_secure_cookies_and_proxy_header(self):
        code, out = load_settings(DJANGO_HTTPS="1")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["session_secure"])
        self.assertTrue(out["csrf_secure"])
        self.assertEqual(out["proxy_header"], ["HTTP_X_FORWARDED_PROTO", "https"])

    def test_database_cache_is_shared_between_gunicorn_workers(self):
        code, out = load_settings(CACHE_BACKEND="db")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["cache"], "django.core.cache.backends.db.DatabaseCache")
        code, out = load_settings()
        self.assertEqual(out["cache"], "django.core.cache.backends.locmem.LocMemCache")

    def test_origins_and_hosts_come_from_environment(self):
        code, out = load_settings(DJANGO_CSRF_TRUSTED_ORIGINS="http://1.2.3.4, https://example.com",
                                  DJANGO_ALLOWED_HOSTS="1.2.3.4,example.com")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["csrf_origins"], ["http://1.2.3.4", "https://example.com"])
        self.assertEqual(out["hosts"], ["1.2.3.4", "example.com"])


def parse_env(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


class TolerantStorageTests(SimpleTestCase):
    def make(self, tmp):
        from apps.core.storage import TolerantManifestStaticFilesStorage

        return TolerantManifestStaticFilesStorage(location=tmp, base_url="/static/")

    def test_directory_paths_are_returned_as_is(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "vendor" / "bootswatch").mkdir(parents=True)
            storage = self.make(tmp)
            self.assertEqual(storage.url("vendor/bootswatch"), "/static/vendor/bootswatch")
            self.assertEqual(storage.url("vendor/nowhere"), "/static/vendor/nowhere")

    def test_files_stay_strict(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.make(tmp).url("missing/file.css")


class DeployFilesTests(SimpleTestCase):
    def read(self, name: str) -> str:
        return (DEPLOY / name).read_text(encoding="utf-8")

    def test_env_template_loads_as_valid_production_settings(self):
        """Шаблон .env, у який скрипт підставив секрети, має давати робочі бойові налаштування."""
        text = (self.read("env.production.template")
                .replace("__SECRET_KEY__", STRONG_KEY).replace("__DB_PASSWORD__", "a" * 48)
                .replace("__SERVER_NAME__", "185.233.44.100"))
        env = parse_env(text)
        self.assertNotIn("__", text)
        self.assertEqual(env["DJANGO_DEBUG"], "0")
        # Секрети користувача (ключ Gemini, токен бота) шаблон лишає порожніми: їх вписує власник сам.
        self.assertEqual(env["GEMINI_API_KEY"], "")
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "")
        env.pop("DB_HOST"), env.pop("DB_PORT")
        code, out = load_settings(**{**env, "DB_ENGINE": "sqlite"})
        self.assertEqual(code, 0, out)
        self.assertEqual(out["csrf_origins"], ["http://185.233.44.100"])
        self.assertEqual(out["hosts"], ["185.233.44.100"])
        self.assertEqual(out["cache"], "django.core.cache.backends.db.DatabaseCache")

    def test_env_template_only_sets_variables_the_project_reads(self):
        source = (BASE / "config" / "settings.py").read_text(encoding="utf-8")
        for key in parse_env(self.read("env.production.template")):
            self.assertIn(f'"{key}"', source, f"{key} з шаблону .env не читається в settings.py")

    def test_env_template_has_no_real_secrets(self):
        env = parse_env(self.read("env.production.template"))
        for key in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME"):
            self.assertEqual(env[key], "")
        self.assertRegex(env["DJANGO_SECRET_KEY"], r"^__[A-Z_]+__$")
        self.assertRegex(env["DB_PASSWORD"], r"^__[A-Z_]+__$")

    def test_services_are_memory_limited_and_loopback_only(self):
        web, bot = self.read("autohubkr-web.service"), self.read("autohubkr-bot.service")
        for unit in (web, bot):
            self.assertRegex(unit, r"(?m)^MemoryMax=\d+M$")
            self.assertIn("User=deploy", unit)
            self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("--bind 127.0.0.1:", web)
        self.assertNotIn("0.0.0.0", web)
        self.assertIn("manage.py runbot", bot)

    def test_nginx_proxies_to_the_port_gunicorn_listens_on(self):
        port = re.search(r"--bind 127\.0\.0\.1:(\d+)", self.read("autohubkr-web.service")).group(1)
        self.assertIn(f"proxy_pass http://127.0.0.1:{port};", self.read("nginx-autohubkr.conf"))
        self.assertIn(f"PORT=${{PORT:-{port}}}", self.read("deploy.sh"))

    def test_nginx_serves_only_provider_media(self):
        """Фото заявок і чеки — персональні дані: назовні віддаються лише фото виконавців."""
        conf = self.read("nginx-autohubkr.conf")
        self.assertRegex(conf, r"location /media/providers/ \{\s+alias /srv/autohubkr/app/media/providers/;")
        self.assertRegex(conf, r"location /media/ \{\s+return 404;")
        self.assertRegex(conf, r"location /static/ \{\s+alias /srv/autohubkr/app/staticfiles/;")
        self.assertIn("server_name __SERVER_NAME__;", conf)
        # Чужий default_server не перехоплюємо: відповідаємо лише на свій Host.
        self.assertNotRegex(conf, r"listen[^;]*default_server")
        self.assertIn("X-Forwarded-Proto $scheme", conf)

    def test_deploy_script_is_valid_bash(self):
        bash = shutil.which("bash")
        # На Windows "bash" може виявитися заглушкою WSL без встановленого дистрибутива: тоді перевірку пропускаємо.
        if not bash or subprocess.run([bash, "-c", "exit 0"], capture_output=True).returncode:
            self.skipTest("робочий bash не знайдено")
        done = subprocess.run([bash, "-n", "deploy/deploy.sh"], cwd=BASE, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_deploy_script_is_additive_on_a_shared_server(self):
        script = self.read("deploy.sh")
        # Спільний сервер: жодних глобальних змін системи.
        for forbidden in ("apt upgrade", "apt-get upgrade", "dist-upgrade", "ufw ", "systemctl restart nginx",
                          "rm -rf /", "DROP DATABASE", "DROP ROLE", "iptables"):
            self.assertNotIn(forbidden, script)
        self.assertIn("nginx -t", script)
        self.assertIn("systemctl reload nginx", script)
        # Існуючий .env не перезаписується.
        self.assertIn("[ -f .env ]", script)
        # umask 077 лише в підоболонці: інакше media/ і статика стають недоступні nginx (www-data).
        self.assertRegex(script, r"\(\s*\n\s*umask 077")
        self.assertEqual(script.count("umask 077"), 1)
        self.assertRegex(script, r'chmod o\+x "\$BASE" "\$APP" media')
        # Бот вмикається лише коли токен уже вписано.
        self.assertIn('env_value TELEGRAM_BOT_TOKEN', script)
