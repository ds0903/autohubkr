"""Єдине місце створення клієнта Gemini: ключ і обмеження часу очікування.

Без таймауту повільна відповідь (спостерігали 30+ с замість звичних 4–10 с) вішає користувача на сайті чи в боті.
"""
from django.conf import settings

# Gemini API відхиляє запити з дедлайном менше 10 с (400 «Minimum allowed deadline is 10s»)
MIN_TIMEOUT_SECONDS = 10


def make_client():
    from google import genai
    from google.genai import types

    seconds = max(float(settings.GEMINI_TIMEOUT_SECONDS), MIN_TIMEOUT_SECONDS)
    # HttpOptions.timeout у google-genai задається в мілісекундах
    return genai.Client(api_key=settings.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=int(seconds * 1000)))
