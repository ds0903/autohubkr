import asyncio
import logging

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Запускає Telegram-бота (long polling). Потрібен TELEGRAM_BOT_TOKEN у .env."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from apps.bot.runner import main

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            self.stdout.write("Бот зупинено.")
