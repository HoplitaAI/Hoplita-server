import re
from datetime import timedelta

from django.db import connection
from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.timesince import timesince

from . import protocol
from .models import (Contact, Envelope, Identity, InviteCode, QuotaTier,
                     RegistrationPolicy, ServerNotice, SyncSnapshot)

TESTY_RE = re.compile(r"test|prueba|demo|tmp|borrar", re.I)

WINDOW_DAYS = 14
STUCK_DAYS = 7
NEAR_QUOTA = 0.8


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def ago(when, now=None):
    if not when:
        return "never"
    now = now or timezone.now()
    if now - when < timedelta(minutes=1):
        return "just now"
    return timesince(when, now).split(",")[0] + " ago"


def bucket(ident, now):
    if (ident._nc == 0 and ident._ns == 0
            and ident.created_at < now - timedelta(hours=48)):
        return "tried"
    if ident.last_seen and ident.last_seen >= now - timedelta(days=7):
        return "alive"
    if ident.last_seen and ident.last_seen >= now - timedelta(days=30):
        return "asleep"
    return "ghost"


BUCKET_LABEL = {"alive": "active", "asleep": "asleep", "ghost": "gone quiet",
                "tried": "tried and left"}


def bars_svg(values, labels, empty_hint):
    top = max(values) if values else 0
    if not top:
        return mark_safe(f"<div class='hd-empty'>No data in this window "
                         f"— {escape(empty_hint)}</div>")
    W, H, n = 720, 160, len(values)
    step = W / n
    bw = max(step - 6, 3)
    out = [f"<svg viewBox='0 0 {W} {H}' class='hd-chart' role='img'>",
           f"<line x1='0' y1='{H - 22}' x2='{W}' y2='{H - 22}' class='hd-axis'/>",
           f"<text x='{W - 2}' y='11' class='hd-max'>max {top}</text>"]
    for i, v in enumerate(values):
        h = (H - 34) * v / top
        x = i * step + 3
        out.append(f"<rect x='{x:.1f}' y='{H - 22 - h:.1f}' width='{bw:.1f}' "
                   f"height='{max(h, 1.5):.1f}' rx='3' class='hd-bar'>"
                   f"<title>{escape(labels[i])}: {v}</title></rect>")
        if n <= 24 or i % max(n // 12, 1) == 0:
            out.append(f"<text x='{x + bw / 2:.1f}' y='{H - 6}' "
                       f"class='hd-tick'>{escape(labels[i])}</text>")
    out.append("</svg>")
    return mark_safe("".join(out))


def _admin(name, *args):
    return reverse(f"admin:federation_{name}", args=args)


def build(request):
    now = timezone.now()
    since = now - timedelta(days=WINDOW_DAYS)

    pend = {r["recipient"]: (r["n"], r["b"] or 0) for r in
            Envelope.objects.filter(delivered=False).values("recipient")
            .annotate(n=Count("id"), b=Sum("size_hint"))}
    outbox = {r["sender"]: (r["n"], r["b"] or 0) for r in
              Envelope.objects.filter(delivered=False).values("sender")
              .annotate(n=Count("id"), b=Sum("size_hint"))}
    sent = {r["sender"]: r["n"] for r in
            Envelope.objects.values("sender").annotate(n=Count("id"))}
    conts = {r["owner"]: r["n"] for r in
             Contact.objects.values("owner").annotate(n=Count("id"))}
    idents = list(Identity.objects.select_related("invite").only(
        "id", "handle", "created_at", "last_seen", "ws_online", "tier_id",
        "quota_overrides", "invite__note", "invite__code"))
    for i in idents:
        i._pend, i._pbytes = pend.get(i.id, (0, 0))
        i._ns = sent.get(i.id, 0)
        i._nc = conts.get(i.id, 0)
        i._bucket = bucket(i, now)

    counts = {b: 0 for b in BUCKET_LABEL}
    for i in idents:
        counts[i._bucket] += 1
    tried = [i for i in idents if i._bucket == "tried"]
    named_like_tests = [i for i in idents if TESTY_RE.search(i.handle)]
    online = sum(1 for i in idents if i.is_online)
    new_7d = sum(1 for i in idents if i.created_at >= now - timedelta(days=7))

    tiers = {t.pk: t for t in QuotaTier.objects.all()}
    default_tier = next((t for t in tiers.values() if t.is_default), None)
    near_quota = []
    for i in idents:
        n, b = outbox.get(i.id, (0, 0))
        if not n:
            continue
        tier = tiers.get(i.tier_id) or default_tier
        lim = (tier.max_inbox_bytes if tier else 0) or 0
        ov = (i.quota_overrides or {}).get("max_inbox_bytes")
        if ov is not None:
            try:
                lim = int(ov)
            except (TypeError, ValueError):
                pass
        if lim and b >= NEAR_QUOTA * lim:
            near_quota.append({"handle": i.handle, "pk": i.pk,
                               "pct": min(100, round(100 * b / lim)),
                               "used": human_bytes(b),
                               "limit": human_bytes(lim)})
    near_quota.sort(key=lambda r: -r["pct"])

    flight = Envelope.objects.filter(delivered=False).aggregate(
        n=Count("id"), b=Sum("size_hint"), oldest=Min("created_at"),
        stuck=Count("id", filter=Q(created_at__lt=now - timedelta(
            days=STUCK_DAYS))))
    window = Envelope.objects.filter(created_at__gte=since)
    hours = [0] * 24
    for r in (window.annotate(h=ExtractHour("created_at")).values("h")
              .annotate(n=Count("id"))):
        if r["h"] is not None:
            hours[int(r["h"])] += r["n"]
    per_day = {r["d"]: r["n"] for r in
               window.annotate(d=TruncDate("created_at")).values("d")
               .annotate(n=Count("id")) if r["d"]}
    days = [(now - timedelta(days=k)).date()
            for k in range(WINDOW_DAYS - 1, -1, -1)]
    day_values = [per_day.get(d, 0) for d in days]
    total_window = sum(day_values)
    types = list(window.values("resource_type").annotate(n=Count("id"))
                 .order_by("-n")[:8])
    for t in types:
        t["pct"] = round(100 * t["n"] / total_window) if total_window else 0

    policy = RegistrationPolicy.current()
    usable = list(InviteCode.usable().order_by("created_at"))
    expiring = [c for c in usable if c.expires_at
                and c.expires_at <= now + timedelta(days=7)]
    signups_window = sum(1 for i in idents if i.created_at >= since)

    notices = list(ServerNotice.objects.select_related("recipient")
                   .filter(active=True)
                   .filter(Q(expires_at__isnull=True)
                           | Q(expires_at__gt=now))
                   .order_by("-created_at")[:3])
    snaps = SyncSnapshot.objects.aggregate(n=Count("id"), b=Sum("size"),
                                           last=Max("updated_at"))
    db_size = None
    if connection.vendor == "postgresql":
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_database_size(current_database())")
                db_size = cur.fetchone()[0]
        except Exception:
            db_size = None

    attention = []
    if policy.mode == policy.INVITE and not usable:
        attention.append(("danger", "Nobody can sign up",
                          "Sign-up is by invitation and there is no usable "
                          "code.", _admin("invitecode_add"), "Create a code"))
    if flight["stuck"]:
        attention.append(("warning", f"{flight['stuck']} envelope(s) "
                          f"waiting more than {STUCK_DAYS} days",
                          "Their recipients have not connected to collect "
                          "them; they keep counting against the sender.",
                          _admin("envelope_changelist") + "?delivered__exact=0",
                          "See them"))
    if near_quota:
        attention.append(("warning", f"{len(near_quota)} account(s) near "
                          "their sending quota",
                          ", ".join(r["handle"] for r in near_quota[:4]),
                          _admin("identity_changelist"), "Users"))
    for c in expiring:
        attention.append(("info", f"Code \"{c.note or c.code}\" expires "
                          f"in {timesince(now, c.expires_at).split(',')[0]}",
                          f"{c.uses} of {c.max_uses or '∞'} used.",
                          _admin("invitecode_change", c.pk), "Open"))
    if tried:
        attention.append(("info", f"{len(tried)} account(s) tried and left",
                          "No contacts and nothing sent, more than 48 h "
                          "after signing up. Candidates for clean-up.",
                          "#tried", "List"))

    newest = sorted(idents, key=lambda i: i.created_at, reverse=True)[:6]
    fullest = sorted((i for i in idents if i._pend),
                     key=lambda i: i._pbytes, reverse=True)[:6]

    return {
        "now": now,
        "host": request.get_host(),
        "tz": timezone.get_current_timezone_name(),
        "kpi": {
            "accounts": len(idents), "new_7d": new_7d, "online": online,
            "alive": counts["alive"], "asleep": counts["asleep"],
            "ghost": counts["ghost"], "tried": counts["tried"],
            "flight_n": flight["n"] or 0,
            "flight_b": human_bytes(flight["b"]),
            "flight_oldest": ago(flight["oldest"], now) if flight["oldest"]
            else "",
            "window_total": total_window,
        },
        "attention": attention,
        "chart_days": bars_svg(day_values, [d.strftime("%d/%m") for d in days],
                               "envelopes appear here as soon as there are"),
        "chart_hours": bars_svg(hours, [f"{h:02d}" for h in range(24)],
                                "envelopes appear here as soon as there are"),
        "types": types,
        "signup": {
            "mode": str(policy), "mode_key": policy.mode,
            "usable": len(usable), "window": signups_window,
            "policy_url": _admin("registrationpolicy_change", policy.pk),
            "codes_url": _admin("invitecode_changelist"),
            "add_url": _admin("invitecode_add"),
        },
        "newest": [{"handle": i.handle, "pk": i.pk,
                    "ago": ago(i.created_at, now),
                    "invite": (i.invite.note or i.invite.code)
                    if i.invite else "",
                    "state": i._bucket,
                    "state_label": BUCKET_LABEL[i._bucket]} for i in newest],
        "fullest": [{"handle": i.handle, "pk": i.pk, "n": i._pend,
                     "size": human_bytes(i._pbytes)} for i in fullest],
        "tried": [{"handle": i.handle, "pk": i.pk,
                   "created": ago(i.created_at, now),
                   "seen": ago(i.last_seen, now)} for i in tried[:20]],
        "tried_more": max(0, len(tried) - 20),
        "named_like_tests": [i.handle for i in named_like_tests[:12]],
        "named_like_tests_n": len(named_like_tests),
        "notices": [{"pk": n.pk, "kind": n.get_kind_display(),
                     "level": n.level,
                     "text": (n.text or f"{n.code} {n.params}")[:140],
                     "to": n.recipient.handle if n.recipient_id else "everyone"}
                    for n in notices],
        "notices_url": _admin("servernotice_changelist"),
        "notice_add_url": _admin("servernotice_add"),
        "server": {
            "proto": protocol.PROTO, "min_proto": protocol.MIN_PROTO,
            "db": connection.vendor,
            "db_size": human_bytes(db_size) if db_size is not None else "",
            "snapshots": snaps["n"] or 0,
            "snapshots_b": human_bytes(snaps["b"]),
        },
    }


def dashboard_callback(request, context):
    context.update({"hd": build(request)})
    return context
