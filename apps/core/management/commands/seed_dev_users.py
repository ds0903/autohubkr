"""Тестові акаунти для ручного огляду сайту й адмінки на локальній машині (по одному на роль).

Лише для розробки: команда відмовляється працювати при DEBUG=False. Паролі навмисно прості й відомі,
тому ці акаунти не можна створювати на справжньому сервері.

    manage.py seed_dev_users

Ролі:
  ivan_test   клієнт: сайт (гараж, заявки, профіль) і Telegram-бот
  owner_demo  виконавець (власник демо-СТО «Північ»): працює в Telegram-боті, сайтового кабінету немає
  liaison     зв'язковий + адміністратор: адмінка /admin/
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.catalog.models import Provider

ACCOUNTS = [
    # (логін, пароль, роль, персонал адмінки, суперкористувач)
    ("ivan_test", "S3cure-pass-77", User.Role.CLIENT, False, False),
    ("owner_demo", "Owner-pass-1", User.Role.PROVIDER, False, False),
    ("liaison", "Liaison-pass-1", User.Role.LIAISON, True, True),
]
DEMO_PROVIDER = "[ДЕМО] СТО «Північ»"


class Command(BaseCommand):
    help = "Створює/оновлює локальні тестові акаунти клієнта, виконавця і зв'язкового (лише DEBUG)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Тестові акаунти з відомими паролями створюються лише при DEBUG=True.")
        for username, password, role, staff, superuser in ACCOUNTS:
            user, created = User.objects.get_or_create(username=username, defaults={"role": role})
            user.role, user.is_staff, user.is_superuser = role, staff, superuser
            user.ai_dialog_consent = role == User.Role.CLIENT   # як за замовчуванням при реєстрації на сайті
            user.set_password(password)
            user.save()
            self.stdout.write(f"{'створено' if created else 'оновлено'}: {username} ({user.get_role_display()})")
        owner = User.objects.get(username="owner_demo")
        linked = Provider.objects.filter(name=DEMO_PROVIDER).update(owner=owner)
        if not linked:
            self.stdout.write(self.style.WARNING("Демо-виконавця немає: спершу `manage.py seed_demo`."))
        self.stdout.write(self.style.SUCCESS("Готово. Паролі дивіться у файлі цієї команди."))
