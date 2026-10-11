import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hoplitafed.settings')

from django.core.asgi import get_asgi_application
django_asgi_app = get_asgi_application()

from hoplitafed import boot_checks
boot_checks.check(strict=True)

from channels.routing import ProtocolTypeRouter, URLRouter
from federation.routing import websocket_urlpatterns

from hoplitafed.limits import HttpConcurrencyLimit

application = ProtocolTypeRouter({
    "http": HttpConcurrencyLimit(django_asgi_app),
    "websocket": URLRouter(websocket_urlpatterns),
})
