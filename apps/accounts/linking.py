"""Прив'язка Telegram до акаунта на сайті через короткочасний підписаний код.

Код короткий і складається лише з літер, цифр і дефісів, тож його можна передати прямим посиланням на бота
(`https://t.me/<бот>?start=link-<код>`): Telegram приймає в `start` до 64 знаків із `A-Za-z0-9_-`.
Код безстановий: його не треба зберігати, бо бот і сайт працюють у різних процесах.
"""
import json
import logging
import time
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.http import int_to_base36

from .models import User

logger = logging.getLogger(__name__)

SALT = "tg-link"
MAX_AGE_SECONDS = 15 * 60
START_PREFIX = "link-"
_USERNAME_CACHE_KEY = "tg-bot-username"


def _mac(uid: str, ts: str) -> str:
    return salted_hmac(SALT, f"{uid}.{ts}", algorithm="sha256").hexdigest()[:20]


def make_token(user: User) -> str:
    uid, ts = int_to_base36(user.pk), int_to_base36(int(time.time()))
    return f"{uid}-{ts}-{_mac(uid, ts)}"


def read_token(token: str) -> User | None:
    token = (token or "").strip()
    if token.startswith(START_PREFIX):
        token = token[len(START_PREFIX):]
    parts = token.split("-")
    if len(parts) != 3:
        return None
    uid, ts, mac = parts
    if not constant_time_compare(mac, _mac(uid, ts)):
        return None
    try:
        pk, issued = int(uid, 36), int(ts, 36)
    except ValueError:
        return None
    if time.time() - issued > MAX_AGE_SECONDS:
        return None
    return User.objects.filter(pk=pk, is_active=True).first()


@transaction.atomic
def link_telegram(tg_id: int, token: str) -> tuple[bool, str]:
    """Прив'язує Telegram ID до сайтового акаунта. Дані «безіменного» tg-акаунта переносяться на сайтовий."""
    from apps.assistant.models import Conversation
    from apps.garage.models import Vehicle
    from apps.orders.models import Order

    site_user = read_token(token)
    if site_user is None:
        return False, "Код недійсний або застарів. Згенеруйте новий у профілі на сайті."
    if site_user.telegram_id == tg_id:
        return True, "Telegram уже підключено до цього акаунта."
    temp = User.objects.filter(telegram_id=tg_id).exclude(pk=site_user.pk).first()
    if temp:
        if temp.role != User.Role.CLIENT or temp.has_usable_password():
            return False, "Цей Telegram уже прив'язаний до іншого повноцінного акаунта."
        Order.objects.filter(client=temp).update(client=site_user)
        Vehicle.objects.filter(owner=temp).update(owner=site_user)
        Conversation.objects.filter(user=temp).update(user=site_user)
        temp.notifications.all().update(user=site_user)
        temp.delete()
    site_user.telegram_id = tg_id
    site_user.save(update_fields=["telegram_id"])
    return True, f"Готово! Акаунт «{site_user.first_name or site_user.username}» підключено до бота."


# ---------- посилання на бота для профілю ----------

def _fetch_bot_username() -> str:
    """Питає в Telegram, як звати бота за його токеном. Токен у журнал і в текст помилок не потрапляє."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe"
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 (фіксований https-хост)
        return (json.load(response).get("result") or {}).get("username", "") or ""


def bot_username() -> str:
    """Ім'я бота без «@» або порожній рядок, якщо бота немає (тоді посилань на нього теж немає).

    Береться з TELEGRAM_BOT_USERNAME; якщо його не задано, а токен є, з'ясовується запитом getMe і запам'ятовується
    (вдало на добу, невдало на 5 хвилин, щоб недоступний Telegram не гальмував кожну відкриту сторінку)."""
    name = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    if name:
        return name
    if not settings.TELEGRAM_BOT_TOKEN:
        return ""
    cached = cache.get(_USERNAME_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        name = _fetch_bot_username()
    except Exception as exc:  # мережа, 401 від Telegram, дивна відповідь
        logger.warning("Не вдалося дізнатися ім'я Telegram-бота: %s", type(exc).__name__)
        name = ""
    cache.set(_USERNAME_CACHE_KEY, name, 24 * 3600 if name else 300)
    return name


def bot_links(user: User) -> dict | None:
    """Для профілю: посилання на бота і (якщо Telegram ще не підключено) прямий лінк з одноразовим кодом."""
    name = bot_username()
    if not name:
        return None
    links = {"bot_url": f"https://t.me/{name}", "linked": bool(user.telegram_id), "link_url": "", "code": "",
             "minutes": MAX_AGE_SECONDS // 60}
    if not user.telegram_id:
        token = make_token(user)
        links["code"] = token
        links["link_url"] = f"https://t.me/{name}?start={START_PREFIX}{token}"
    return links
