"""Сховище статики для бойового режиму."""
import posixpath

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage, StaticFilesStorage


class TolerantManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """Хешовані імена файлів, але шлях без розширення (каталог) віддається як є.

    Jazzmin вставляє в сторінку {% static 'vendor/bootswatch' %}: це каталог із темами, а не файл. Звичайне
    ManifestStaticFilesStorage для такого шляху кидає ValueError («Missing staticfiles manifest entry»), і вся
    адмінка після входу відповідає 500. Для файлів із розширенням сувора перевірка лишається: помилка в назві
    або забутий collectstatic не маскуються.
    """

    def url(self, name, force=False):
        try:
            return super().url(name, force)
        except (ValueError, OSError):
            if posixpath.splitext(name)[1]:
                raise
            return StaticFilesStorage.url(self, name)
