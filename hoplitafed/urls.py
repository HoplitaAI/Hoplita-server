from django.contrib import admin
from django.urls import path, include

from .admin_site import install

install()

from federation.admin import stats_view
from federation.views_health import health
from .views_home import favicon, home, robots

urlpatterns = [
    path("", home),
    path("favicon.ico", favicon),
    path("robots.txt", robots),
    path("admin/stats/", stats_view),
    path("admin/", admin.site.urls),
    path("api/fed/", include("federation.urls")),
    path("health", health),
]
