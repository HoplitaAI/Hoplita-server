import os
import time

from django.conf import settings
from django.db import connection
from django.http import JsonResponse

_STARTED = time.time()


def _backend(name, dotted):
    cfg = getattr(settings, name, {}).get("default", {}) or {}
    return (cfg.get(dotted, "") or "").rsplit(".", 1)[-1] or "?"


def health(request):
    ok = True
    db_ok = True
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
            c.fetchone()
    except Exception:
        db_ok = ok = False

    cache_ok = True
    try:
        from django.core.cache import cache
        cache.set("hoplita:health", 1, 10)
        cache_ok = cache.get("hoplita:health") == 1
    except Exception:
        cache_ok = False
    ok = ok and cache_ok

    layer = _backend("CHANNEL_LAYERS", "BACKEND")
    return JsonResponse({
        "ok": ok,
        "pid": os.getpid(),
        "uptime_s": round(time.time() - _STARTED, 1),
        "db": {"ok": db_ok,
               "engine": settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]},
        "cache": {"ok": cache_ok, "backend": _backend("CACHES", "BACKEND")},
        "channel_layer": layer,
        "multiproc_safe": layer != "InMemoryChannelLayer",
        "debug": settings.DEBUG,
    }, status=200 if ok else 503)
