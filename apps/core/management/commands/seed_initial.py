"""Початкові довідники: місто, категорії виконавців, типи послуг, марки авто, нормативи ТО.

Ідемпотентна команда: можна запускати повторно.
"""
from django.core.management.base import BaseCommand

from apps.catalog.models import CarBrand, Category, ServiceType
from apps.core.models import City
from apps.garage.services import ensure_default_rules

C = Category.RevenueModel

# slug, назва, модель доходу (розділи 4 і 7.3 ТЗ)
CATEGORIES = [
    ("sto", "СТО: діагностика і ремонт", C.COMMISSION),
    ("tires", "Шиномонтаж", C.SUBSCRIPTION),
    ("wash", "Автомийка", C.SUBSCRIPTION),
    ("detailing", "Детейлінг", C.SUBSCRIPTION),
    ("body", "Кузовний ремонт і фарбування", C.COMMISSION),
    ("electrics", "Автоелектрик", C.COMMISSION),
    ("tow", "Евакуатор", C.COMMISSION),
]

# категорія, slug, назва, критично для безпеки, ключові слова
SERVICE_TYPES = [
    ("sto", "diag-chassis", "Діагностика ходової частини", False,
     "стук, стукіт, ходова, підвіска, амортизатор, сайлентблок, скрип, на ямах, кочки, раскачка, гойдає"),
    ("sto", "diag-engine", "Діагностика двигуна", False,
     "двигун, мотор, check engine, чек, троїть, не заводиться, дим, витрата масла, втрата потужності, рвані оберти"),
    ("sto", "brakes", "Гальмівна система", True,
     "гальма, гальмівні, колодки, диски, скрип при гальмуванні, педаль, ABS, тормоза, тормозные"),
    ("sto", "steering", "Рульове управління", True,
     "кермо, руль, люфт, гідропідсилювач, вібрація керма, рульова рейка, тяга"),
    ("sto", "maintenance", "Планове ТО і заміна мастила", False,
     "ТО, технічне обслуговування, мастило, масло, фільтр, планове, замена масла"),
    ("sto", "transmission", "Коробка передач і зчеплення", False,
     "коробка, КПП, АКПП, зчеплення, передачі, ривки, пробуксовка, не вмикається"),
    ("sto", "cooling", "Система охолодження", False,
     "перегрів, антифриз, радіатор, температура, охолодження, закипає"),
    ("sto", "ac", "Кондиціонер і клімат", False,
     "кондиціонер, не холодить, пічка, обігрів, клімат, заправка кондиціонера"),
    ("tires", "tire-fit", "Шиномонтаж і балансування", False,
     "шини, гума, балансування, прокол, перезуття, сезонна заміна, вібрує колесо, диски"),
    ("tires", "alignment", "Розвал-сходження", False,
     "розвал, сходження, тягне вбік, нерівномірний знос шин, кермо криво"),
    ("wash", "wash", "Мийка автомобіля", False, "мийка, помити, чистка кузова, комплексна мийка"),
    ("detailing", "polish", "Полірування і захист кузова", False,
     "полірування, подряпини, кераміка, віск, захисне покриття, блиск"),
    ("detailing", "interior-cleaning", "Хімчистка салону", False,
     "хімчистка, салон, запах, плями, чистка сидінь, озонація"),
    ("body", "body-repair", "Кузовний ремонт", False,
     "кузов, вм'ятина, дтп, після аварії, бампер, крило, геометрія"),
    ("body", "paint", "Фарбування", False, "фарбування, покраска, відколи, іржа, пофарбувати, локальний ремонт"),
    ("electrics", "electrics", "Автоелектрика", False,
     "проводка, електрика, лампочки, світло, фари, не працює, замикання, генератор"),
    ("electrics", "battery-starter", "Акумулятор і стартер", False,
     "акумулятор, не заводиться, стартер, сів, клацає, зарядка"),
    ("electrics", "airbags", "Подушки безпеки (SRS)", True,
     "подушки безпеки, airbag, SRS, горить індикатор подушки, ремінь безпеки"),
    ("tow", "tow", "Евакуація", False, "евакуатор, відбуксирувати, застряг, не їде, зламався на дорозі"),
]

BRANDS = [
    "Toyota", "Volkswagen", "Skoda", "Renault", "Hyundai", "Kia", "BMW", "Mercedes-Benz", "Audi", "Opel", "Ford",
    "Nissan", "Mazda", "Honda", "Peugeot", "Citroen", "Chevrolet", "Daewoo", "Lada", "Mitsubishi", "Subaru", "Volvo",
    "Fiat", "SEAT", "Suzuki", "Land Rover", "Porsche", "Tesla", "Lexus", "Jeep", "Dacia", "Chery", "Geely",
]


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


class Command(BaseCommand):
    help = "Створює початкові довідники (місто, категорії, типи послуг, марки, нормативи ТО)."

    def handle(self, *args, **options):
        City.objects.get_or_create(slug="kryvyi-rih", defaults={"name": "Кривий Ріг"})

        for order, (slug, name, model) in enumerate(CATEGORIES, start=1):
            Category.objects.get_or_create(
                slug=slug, defaults={"name": name, "revenue_model": model, "sort_order": order * 10}
            )

        for cat_slug, slug, name, safety, keywords in SERVICE_TYPES:
            category = Category.objects.get(slug=cat_slug)
            ServiceType.objects.get_or_create(
                slug=slug,
                defaults={"category": category, "name": name, "is_safety_critical": safety, "keywords": keywords},
            )

        for name in BRANDS:
            CarBrand.objects.get_or_create(name=name, defaults={"slug": _slug(name)})

        created_rules = ensure_default_rules()
        self.stdout.write(self.style.SUCCESS(
            f"Готово: категорій {Category.objects.count()}, типів послуг {ServiceType.objects.count()}, "
            f"марок {CarBrand.objects.count()}, нормативів ТО створено {created_rules}."
        ))
