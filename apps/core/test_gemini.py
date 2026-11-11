from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.core import gemini


class MakeClientTests(SimpleTestCase):
    def _timeout_ms(self):
        with mock.patch("google.genai.Client") as client_cls:
            gemini.make_client()
        kwargs = client_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        return kwargs["http_options"].timeout

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_TIMEOUT_SECONDS=12.5)
    def test_timeout_is_passed_in_milliseconds(self):
        self.assertEqual(self._timeout_ms(), 12500)

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_TIMEOUT_SECONDS=2)
    def test_timeout_below_api_minimum_is_raised(self):
        self.assertEqual(self._timeout_ms(), 10000, "API відхиляє дедлайн менше 10 с")
