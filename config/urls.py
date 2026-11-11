from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "АвтоХаб КР: панель зв'язкового"
admin.site.site_title = "АвтоХаб КР"
admin.site.index_title = "Керування платформою"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
