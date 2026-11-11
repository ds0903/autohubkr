"""Перевірка ШІ перед запуском: чи задано ключ і чи відповідає налаштована модель Gemini.

    python manage.py check_ai

Без цього збій (знята модель, закінчилась квота, неправильний ключ) непомітний: сайт мовчки працює на резервному
підборі за ключовими словами.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.assistant import services


class Command(BaseCommand):
    help = "Робить один тестовий запит до Gemini та повідомляє, чи працює налаштована модель."

    def handle(self, *args, **options):
        key = settings.GEMINI_API_KEY
        if not key:
            raise CommandError("GEMINI_API_KEY не задано: ШІ працює лише в резервному режимі (ключові слова).")
        self.stdout.write(f"Модель: {settings.GEMINI_MODEL}")
        try:
            answer = services._call_llm('Поверни JSON {"ok": true}', "Відповідай лише JSON.")
        except Exception as exc:  # noqa: BLE001
            # ключ у текст помилки не потрапляє, але на всяк випадок вирізаємо
            raise CommandError(f"Gemini не відповів: {type(exc).__name__}: {str(exc).replace(key, '***')[:500]}") from exc
        self.stdout.write(self.style.SUCCESS(f"Gemini відповів: {answer}"))
