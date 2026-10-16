
import base64
import os

from django.http import HttpResponse

_HERE = os.path.dirname(os.path.abspath(__file__))
_PAGE = None


def _logo_data_uri():
    try:
        with open(os.path.join(_HERE, "logo.png"), "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode()
    except Exception:
        return ""


def _fallback():
    logo = _logo_data_uri()
    img = (f'<img src="{logo}" alt="HOPLITA" width="112" height="112">'
           if logo else "")
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>HOPLITA</title></head>"
            "<body style=\"margin:0;min-height:100vh;display:grid;place-items:center;"
            "background:#0b1220;color:#e6edf7;font-family:system-ui,sans-serif\">"
            f"<main style=\"text-align:center\">{img}<h1>HOPLITA</h1>"
            "<p><a style=\"color:#22d3ee\" href=\"https://github.com/HoplitaAI/Hoplita\">"
            "github.com/HoplitaAI/Hoplita</a></p></main></body></html>")


def _load():
    try:
        with open(os.path.join(_HERE, "landing.html"), encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return _fallback()


def home(request):
    global _PAGE
    if _PAGE is None:
        _PAGE = _load()
    r = HttpResponse(_PAGE, content_type="text/html; charset=utf-8")
    r["Cache-Control"] = "public, max-age=3600"
    return r


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /api/\nDisallow: /ws/\n"
                        "Disallow: /admin/\nAllow: /\n", content_type="text/plain")


def favicon(request):
    try:
        with open(os.path.join(_HERE, "logo.png"), "rb") as fh:
            r = HttpResponse(fh.read(), content_type="image/png")
    except Exception:
        return HttpResponse(status=404)
    r["Cache-Control"] = "public, max-age=604800"
    return r
