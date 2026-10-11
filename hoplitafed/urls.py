from django.contrib import admin
from django.urls import path, include
from django_otp.admin import OTPAdminSite
from unfold.sites import UnfoldAdminSite

class _HoplitaAdmin(OTPAdminSite, UnfoldAdminSite):


    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        if app_label:
            return apps
        return apps + [{
            "name": "📊 Operation",
            "app_label": "operacion",
            "app_url": "/admin/stats/",
            "has_module_perms": True,
            "models": [{
                "name": "Server statistics",
                "object_name": "stats",
                "admin_url": "/admin/stats/",
                "add_url": None,
                "view_only": True,
                "perms": {"add": False, "change": False,
                          "delete": False, "view": True},
            }],
        }]


admin.site.__class__ = _HoplitaAdmin
admin.site.login_form = OTPAdminSite.login_form

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
