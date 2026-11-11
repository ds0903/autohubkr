from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.catalog.models import Provider


class StaticSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return ["web:home", "web:catalog", "web:privacy"]

    def location(self, item):
        return reverse(item)


class ProviderSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Provider.objects.filter(is_active=True).order_by("pk")

    def lastmod(self, obj):
        return obj.updated_at


SITEMAPS = {"static": StaticSitemap, "providers": ProviderSitemap}
