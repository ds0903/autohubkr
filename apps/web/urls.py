from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap as sitemap_view
from django.urls import path

from . import views
from .sitemaps import SITEMAPS

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("ask/", views.ask_ai, name="ask"),
    path("catalog/", views.catalog, name="catalog"),
    path("catalog/<int:pk>/", views.provider_detail, name="provider"),
    path("privacy/", views.privacy, name="privacy"),
    path("r/<str:code>/", views.referral, name="referral"),
    path("r/<str:code>/qr.svg", views.referral_qr, name="referral_qr"),
    path("confirm/<str:token>/", views.provider_confirm, name="provider_confirm"),
    path("sitemap.xml", sitemap_view, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("robots.txt", views.robots, name="robots"),
    # акаунти
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/profile/", views.profile, name="profile"),
    # заявки
    path("orders/", views.order_list, name="orders"),
    path("orders/new/", views.order_new, name="order_new"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/action/", views.order_action, name="order_action"),
    # гараж
    path("garage/", views.garage, name="garage"),
    path("garage/<int:pk>/", views.vehicle_detail, name="vehicle"),
    path("garage/<int:pk>/record/", views.vehicle_record_add, name="vehicle_record_add"),
    path("garage/<int:pk>/receipt/", views.vehicle_receipt_add, name="vehicle_receipt_add"),
]
