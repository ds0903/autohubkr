"""Налаштування проєкту autohubkr (ШІ-навігатор автопослуг)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEV_SECRET_KEY = "dev-insecure-change-me"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEV_SECRET_KEY)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
# Сайт за nginx: адреси, з яких приймаються POST-форми (з протоколом), наприклад http://185.233.44.100
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Без DEBUG небезпечний типовий ключ неприпустимий: краще впасти при старті, ніж працювати з відомим ключем.
if not DEBUG and (SECRET_KEY == DEV_SECRET_KEY or len(SECRET_KEY) < 32):
    raise ImproperlyConfigured(
        "DJANGO_DEBUG=0, але DJANGO_SECRET_KEY не задано або закороткий (потрібно від 32 знаків)."
    )

INSTALLED_APPS = [
    "jazzmin",  # тема адмінки: має стояти перед django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    # проєктні застосунки
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.orders",
    "apps.garage",
    "apps.reviews",
    "apps.assistant",
    "apps.web",
    "apps.bot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "autohubkr"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# Запасний режим лише для прогону тестів на машинах, де локальний Postgres нестабільний:
#   $env:DB_ENGINE="sqlite"; python manage.py test
# Бойове середовище й розробка працюють на PostgreSQL.
if os.getenv("DB_ENGINE", "postgres").lower() == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:home"
LOGOUT_REDIRECT_URL = "web:home"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk"
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------- Бойове середовище ----------
# Файли статики отримують у назві хеш вмісту (style.3f2a1c.css): після оновлення браузер не показує старий вигляд зі
# свого кешу. Таблиця хешів створюється командою collectstatic, тому режим лише для DEBUG=0.
if not DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
    }

# gunicorn запускає кілька процесів: локальний кеш у кожного свій, тож ліміти запитів до ШІ й кеш імені бота
# рахувалися б окремо. CACHE_BACKEND=db ділить кеш між процесами через БД (потрібна команда createcachetable).
if os.getenv("CACHE_BACKEND", "locmem").strip().lower() == "db":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "django_cache"}}

# Увімкніть DJANGO_HTTPS=1 тільки коли сайт справді відкривається за https (nginx передає X-Forwarded-Proto).
# За простим http (наприклад, за голим IP) безпечні cookie зробили б вхід неможливим.
if env_bool("DJANGO_HTTPS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))

# У бойовому режимі журнал іде в stderr (systemd збирає його в journalctl).
if not DEBUG:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(levelname)s %(name)s: %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
        "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    }

SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

# ---------- Адмінка (панель зв'язкового) ----------
JAZZMIN_SETTINGS = {
    "site_title": "АвтоХаб КР · панель",
    "site_header": "АвтоХаб",
    "site_brand": "АвтоХаб КР",
    "welcome_sign": "Панель зв'язкового: виконавці, заявки та відгуки",
    "copyright": "АвтоХаб КР",
    "search_model": [],
    "user_avatar": None,
    "topmenu_links": [
        {"name": "Переглянути сайт", "url": "/", "new_window": True},
        {"name": "Звіт пілота", "url": "admin:orders_order_report", "permissions": ["orders.view_order"]},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_models": ["auth.group"],
    # спочатку те, чим зв'язковий користується щодня
    "order_with_respect_to": ["orders", "catalog", "reviews", "garage", "accounts", "assistant", "core"],
    "icons": {
        "accounts.User": "fas fa-user-shield",
        "orders.Order": "fas fa-clipboard-list",
        "orders.Offer": "fas fa-comments-dollar",
        "orders.BypassReport": "fas fa-user-secret",
        "catalog.Provider": "fas fa-wrench",
        "catalog.ProviderPhoto": "fas fa-images",
        "catalog.Category": "fas fa-tags",
        "catalog.ServiceType": "fas fa-screwdriver-wrench",
        "catalog.CarBrand": "fas fa-car",
        "reviews.Review": "fas fa-star",
        "garage.Vehicle": "fas fa-car-side",
        "garage.GarageEntry": "fas fa-book",
        "garage.MaintenanceRule": "fas fa-calendar-check",
        "garage.Receipt": "fas fa-receipt",
        "assistant.Conversation": "fas fa-robot",
        "core.City": "fas fa-city",
        "core.Notification": "fas fa-bell",
    },
    "default_icon_parents": "fas fa-folder",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": False,
    "site_logo": "core/logo.svg",
    "login_logo": "core/logo.svg",
    "site_logo_classes": "rounded",
    "custom_css": "core/admin.css",
    "custom_js": "core/admin.js",
    "show_ui_builder": False,
    "changeform_format": "horizontal_tabs",
    # у заявці занадто багато вкладок для рядка: розділи йдуть згортними блоками
    "changeform_format_overrides": {"orders.order": "collapsible", "accounts.user": "collapsible"},
}

JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": False,
    "footer_small_text": False,
    "body_small_text": False,
    "brand_small_text": False,
    "brand_colour": "navbar-dark",
    "accent": "accent-primary",
    "navbar": "navbar-dark",
    "no_navbar_border": True,
    "navbar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": True,
    "theme": "flatly",
    "dark_mode_theme": None,
    "default_theme_mode": "light",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-outline-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success",
    },
}

# Зовнішні сервіси
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# скільки секунд чекати відповідь Gemini, перш ніж перейти на резервний підбір (звичайно 6–10 с, раз бачили 32 с)
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))
# бюджет «мислення» для підбору послуг: 0 = вимкнено (відповідь ~4 с замість ~8 с, якість та сама для цього завдання),
# порожньо = типова поведінка моделі. Для розпізнавання чеків не застосовується.
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "0").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# ім'я бота без «@» (для посилання t.me у профілі). Не обов'язково: якщо є токен, ім'я з'ясовується запитом getMe.
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
