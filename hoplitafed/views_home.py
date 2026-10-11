
import base64
import os

from django.http import HttpResponse

TEXT = {
    "title": "HOPLITA",
    "soon": "Coming soon",
    "alt": "HOPLITA — hoplite helmet",
}

_HERE = os.path.dirname(os.path.abspath(__file__))
_PAGE = None


def _logo_data_uri():
    try:
        with open(os.path.join(_HERE, "logo.png"), "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode()
    except Exception:
        return ""


def _render():
    logo = _logo_data_uri()
    img = (f'<img src="{logo}" alt="{TEXT["alt"]}" width="112" height="112">'
           if logo else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TEXT["title"]}</title>
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<meta name="color-scheme" content="dark">
{f'<link rel="icon" href="{logo}">' if logo else ''}
<style>
  *,*::before,*::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100dvh; display: grid; place-items: center;
    padding: 2rem 1.25rem;
    background: radial-gradient(120% 90% at 50% 0%, #16233d 0%, #0b1220 60%);
    color: #e6edf7;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    text-align: center; line-height: 1.6;
  }}
  main {{ max-width: 34rem; animation: rise .6s ease-out both; }}
  img {{ width: clamp(88px, 22vw, 112px); height: auto; }}
  h1 {{
    margin: 1.1rem 0 .3rem; font-size: clamp(2rem, 9vw, 2.9rem);
    font-weight: 700; letter-spacing: .22em; text-indent: .22em;
  }}
  .soon {{
    display: inline-block; margin-top: 1.8rem; padding: .42rem 1.1rem;
    border: 1px solid #26364f; border-radius: 999px;
    color: #7c8db0; font-size: .8rem; letter-spacing: .14em;
    text-transform: uppercase;
  }}
  @keyframes rise {{ from {{ opacity: 0; transform: translateY(10px); }} }}
  @media (prefers-reduced-motion: reduce) {{ main {{ animation: none; }} }}
</style>
</head>
<body>
<main>
  {img}
  <h1>{TEXT["title"]}</h1>
  <span class="soon">{TEXT["soon"]}</span>
</main>
</body>
</html>
"""


def home(request):
    global _PAGE
    if _PAGE is None:
        _PAGE = _render()
    r = HttpResponse(_PAGE, content_type="text/html; charset=utf-8")
    r["Cache-Control"] = "public, max-age=3600"
    r["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    return r


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


def favicon(request):
    try:
        with open(os.path.join(_HERE, "logo.png"), "rb") as fh:
            r = HttpResponse(fh.read(), content_type="image/png")
    except Exception:
        return HttpResponse(status=404)
    r["Cache-Control"] = "public, max-age=604800"
    return r
