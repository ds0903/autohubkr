"""Спільні фабрики для тестів."""
from apps.accounts.models import User
from apps.catalog.models import CarBrand, Category, Provider, Service, ServiceType
from apps.core.models import City
from apps.garage.models import Vehicle


def make_world():
    """Мінімальний світ: місто, категорія, типи послуг, виконавець з прайсом, клієнт з авто."""
    city = City.objects.create(name="Кривий Ріг", slug="kr")
    cat = Category.objects.create(name="СТО", slug="sto")
    brakes = ServiceType.objects.create(
        category=cat, name="Гальмівна система", slug="brakes", is_safety_critical=True,
        keywords="гальма, гальмівні, колодки, диски",
    )
    chassis = ServiceType.objects.create(
        category=cat, name="Діагностика ходової", slug="diag-chassis", keywords="стук, ходова, підвіска, амортизатор",
    )
    toyota = CarBrand.objects.create(name="Toyota", slug="toyota")
    provider = Provider.objects.create(city=city, name="СТО Тест", district="Інгулецький", phone="+380501112233")
    provider.categories.add(cat)
    provider.brands.add(toyota)
    Service.objects.create(provider=provider, service_type=chassis, name="Діагностика ходової", price_min=300, price_max=500)
    client = User.objects.create_user("client1", password="pass12345", first_name="Олег", city=city)
    vehicle = Vehicle.objects.create(owner=client, brand=toyota, model_name="Camry", year=2015, mileage=150000)
    return dict(city=city, cat=cat, brakes=brakes, chassis=chassis, toyota=toyota, provider=provider,
                client=client, vehicle=vehicle)
