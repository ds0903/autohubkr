"""ДЕМО-виконавці для розробки та показу. НЕ реальні дані.

Усі демо-записи позначені `source_note = "ДЕМО-ДАНІ"` і видаляються командою `seed_demo --clear`.
Реальну базу (100–150 точок) збирає зв'язковий вручну, розділ 9 ТЗ.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.catalog.models import CarBrand, Category, Provider, Service, ServiceType
from apps.core.models import City

MARK = "ДЕМО-ДАНІ"

DEMO = [
    ("[ДЕМО] СТО «Північ»", "sto", "Інгулецький", "вул. Демонстраційна, 1", ["Toyota", "Lexus"], [
        ("diag-chassis", "Діагностика ходової", 300, 500), ("brakes", "Заміна гальмівних колодок", 600, 1200),
        ("maintenance", "Заміна мастила і фільтра", 400, 900)]),
    ("[ДЕМО] СТО «Центр-Авто»", "sto", "Центрально-Міський", "вул. Демонстраційна, 2", [], [
        ("diag-engine", "Комп'ютерна діагностика двигуна", 350, 600), ("diag-chassis", "Діагностика ходової", 250, 450),
        ("transmission", "Заміна зчеплення", 3500, 7000)]),
    ("[ДЕМО] Німецький сервіс", "sto", "Тернівський", "вул. Демонстраційна, 3", ["Volkswagen", "Audi", "Skoda", "BMW"], [
        ("diag-engine", "Діагностика двигуна", 500, 800), ("steering", "Діагностика рульового управління", 300, 600)]),
    ("[ДЕМО] Шиномонтаж 24", "tires", "Саксаганський", "вул. Демонстраційна, 4", [], [
        ("tire-fit", "Сезонна заміна коліс (R15–R17)", 400, 700), ("alignment", "Розвал-сходження", 600, 900)]),
    ("[ДЕМО] Мийка «Блиск»", "wash", "Покровський", "вул. Демонстраційна, 5", [], [
        ("wash", "Комплексна мийка", 250, 450)]),
    ("[ДЕМО] Детейлінг-студія", "detailing", "Центрально-Міський", "вул. Демонстраційна, 6", [], [
        ("polish", "Полірування кузова", 3000, 6000), ("interior-cleaning", "Хімчистка салону", 1800, 3000)]),
    ("[ДЕМО] Кузовний цех", "body", "Довгинцівський", "вул. Демонстраційна, 7", [], [
        ("paint", "Фарбування елемента", 1800, 3500), ("body-repair", "Кузовний ремонт", 0, 0)]),
    ("[ДЕМО] Автоелектрик Сергій", "electrics", "Інгулецький", "вул. Демонстраційна, 8", [], [
        ("battery-starter", "Діагностика акумулятора і стартера", 200, 400), ("electrics", "Діагностика електрики", 300, 600)]),
    ("[ДЕМО] Евакуатор 24/7", "tow", "по місту", "", [], [("tow", "Евакуація по місту", 600, 1000)]),
]


class Command(BaseCommand):
    help = "Створює (або з --clear видаляє) ДЕМО-виконавців для розробки."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Видалити всі демо-записи.")

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = Provider.objects.filter(source_note=MARK).delete()
            self.stdout.write(self.style.WARNING(f"Видалено демо-записів (з пов'язаними): {deleted}"))
            return

        city = City.objects.filter(slug="kryvyi-rih").first()
        if not city:
            self.stderr.write("Спершу виконайте: manage.py seed_initial")
            return
        for name, cat, district, address, brands, services in DEMO:
            provider, created = Provider.objects.get_or_create(
                name=name, defaults={"city": city, "district": district, "address": address, "source_note": MARK,
                                     "phone": "+380000000000", "working_hours": "Пн–Сб 9:00–18:00"})
            if not created:
                continue
            provider.categories.add(Category.objects.get(slug=cat))
            for b in brands:
                provider.brands.add(CarBrand.objects.get(name=b))
            for slug, title, lo, hi in services:
                Service.objects.create(
                    provider=provider, service_type=ServiceType.objects.get(slug=slug), name=title,
                    price_min=Decimal(lo) if lo else None, price_max=Decimal(hi) if hi else None)
        self.stdout.write(self.style.SUCCESS(f"Демо-виконавців: {Provider.objects.filter(source_note=MARK).count()}"))
