import re
from datetime import timedelta

from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import StackedInline as UnfoldStackedInline
from unfold.contrib.forms.widgets import WysiwygWidget
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from .models import (Identity, EmailPin, Contact, Envelope, SpawnToken,
                     SyncSnapshot, ServerNotice, QuotaTier, PublicFolder,
                     BannedEmail)

_TESTY_RE = re.compile(r"test|prueba|demo|tmp|borrar", re.I)


@admin.register(BannedEmail)
class BannedEmailAdmin(UnfoldModelAdmin):
    list_display = ("email", "reason", "created_at")
    search_fields = ("email", "reason")
    actions = ["deactivate_accounts"]

    @admin.action(description="Deactivate the accounts using these emails")
    def deactivate_accounts(self, request, queryset):
        from django.contrib.auth.models import User
        n = 0
        for ban in queryset:
            n += User.objects.filter(email__iexact=ban.email,
                                     is_active=True).update(is_active=False)
        self.message_user(request, f"{n} account(s) deactivated.")


@admin.register(PublicFolder)
class PublicFolderAdmin(UnfoldModelAdmin):
    list_display = ("folder_name", "owner", "kind", "subs", "created_at")
    search_fields = ("folder_name", "owner__handle")

    def subs(self, obj):
        return obj.subscriber_count()
    subs.short_description = "subscribers"


@admin.register(QuotaTier)
class QuotaTierAdmin(UnfoldModelAdmin):
    list_display = ("name", "is_default", "max_sync_bytes", "max_inbox_envelopes",
                    "max_inbox_bytes", "max_contacts", "max_children",
                    "max_requests_per_min")
    list_editable = ("is_default", "max_sync_bytes", "max_inbox_envelopes",
                     "max_inbox_bytes", "max_contacts", "max_children",
                     "max_requests_per_min")


class QuotaOverrideForm(forms.ModelForm):
    ov_inbox_mb = forms.IntegerField(
        required=False, min_value=0, label="Mailbox in flight (MB)",
        help_text="Empty = whatever their group says. This is what they pay "
                  "for when SENDING.")
    ov_envelopes = forms.IntegerField(
        required=False, min_value=0, label="Envelopes in flight (count)")
    ov_contacts = forms.IntegerField(
        required=False, min_value=0, label="Contacts (count)")
    ov_sync_mb = forms.IntegerField(
        required=False, min_value=0, label="Sync snapshot (MB)")

    _MAP = (("ov_inbox_mb", "max_inbox_bytes", 1024 * 1024),
            ("ov_envelopes", "max_inbox_envelopes", 1),
            ("ov_contacts", "max_contacts", 1),
            ("ov_sync_mb", "max_sync_bytes", 1024 * 1024))

    class Meta:
        model = Identity
        fields = "__all__"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        ov = (self.instance.quota_overrides or {}) if self.instance else {}
        for field, key, unit in self._MAP:
            if key in ov:
                try:
                    self.fields[field].initial = int(ov[key]) // unit
                except (TypeError, ValueError):
                    pass

    def save(self, commit=True):
        obj = super().save(commit=False)
        ov = dict(obj.quota_overrides or {})
        for field, key, unit in self._MAP:
            val = self.cleaned_data.get(field)
            if val in (None, ""):
                ov.pop(key, None)
            else:
                ov[key] = int(val) * unit
        obj.quota_overrides = ov
        if commit:
            obj.save()
        return obj


@admin.register(Identity)
class IdentityAdmin(UnfoldModelAdmin):
    list_display = ("handle", "state", "tier", "quota", "in_flight",
                    "pending", "email_verified", "is_online", "last_seen")

    def get_queryset(self, request):
        from django.db.models import IntegerField, OuterRef, Subquery
        from django.db.models.functions import Coalesce
        from .models import Contact, Envelope

        def _count(qs, group):
            return Coalesce(Subquery(
                qs.order_by().values(group).annotate(c=Count("pk"))
                .values("c"), output_field=IntegerField()), 0)

        return super().get_queryset(request).annotate(
            _pend=_count(Envelope.objects.filter(recipient=OuterRef("pk"),
                                                 delivered=False),
                         "recipient"),
            _nc=_count(Contact.objects.filter(owner=OuterRef("pk")), "owner"),
            _ns=_count(Envelope.objects.filter(sender=OuterRef("pk")),
                       "sender"))

    @admin.display(description="State")
    def state(self, obj):
        now = timezone.now()
        if (obj._nc == 0 and obj._ns == 0
                and obj.created_at < now - timedelta(hours=48)):
            return "🧪 tried and forgot"
        if obj.last_seen and obj.last_seen >= now - timedelta(days=7):
            return "🟢 alive"
        if obj.last_seen and obj.last_seen >= now - timedelta(days=30):
            return "🟡 asleep"
        return "⚰ ghost"

    @admin.display(description="Pending envelopes", ordering="_pend")
    def pending(self, obj):
        return obj._pend

    @admin.display(description="Quota (mailbox limit)")
    def quota(self, obj):
        from .quotas import effective_quota
        lim = effective_quota(obj)["max_inbox_bytes"]
        ov = (obj.quota_overrides or {}).get("max_inbox_bytes")
        return f"{(lim or 0) // 1024} KB" + (" ✏" if ov else "")

    @admin.display(description="In flight (what they pay)")
    def in_flight(self, obj):
        from .quotas import current_usage
        return f"{current_usage(obj).get('outbox_bytes', 0) // 1024} KB"
    list_filter = ("tier", "email_verified")
    list_editable = ("tier",)
    search_fields = ("handle",)
    form = QuotaOverrideForm
    fieldsets = (
        ("Who they are", {"fields": ("handle", "public_key", "fingerprint",
                                     "email_verified")}),
        ("Their quota", {
            "description": "The GROUP sets the limits for everyone in it. The "
                           "numbers below are ONLY for this person: empty "
                           "means the group rules.",
            "fields": ("tier", "ov_inbox_mb", "ov_envelopes", "ov_contacts",
                       "ov_sync_mb", "usage_view")}),
    )
    readonly_fields = ("handle", "public_key", "fingerprint", "usage_view")

    def usage_view(self, obj):
        from .quotas import effective_quota, current_usage
        if obj.pk is None:
            return "—"
        lim, use = effective_quota(obj), current_usage(obj)
        rows = [("sync_bytes", use["sync_bytes"], lim["max_sync_bytes"]),
                ("inbox_envelopes (received, free)", use["inbox_envelopes"], 0),
                ("inbox_bytes (received, free)", use["inbox_bytes"], 0),
                ("outbox_envelopes (in flight, charged)",
                 use.get("outbox_envelopes", 0), lim["max_inbox_envelopes"]),
                ("outbox_bytes (in flight, charged)",
                 use.get("outbox_bytes", 0), lim["max_inbox_bytes"]),
                ("contacts", use["contacts"], lim["max_contacts"]),
                ("children", use["children"], lim["max_children"])]
        return "\n".join(f"{r}: {u} / {l or '∞'}" for r, u, l in rows)
    usage_view.short_description = "Current usage / limit"

class IdentityInline(UnfoldStackedInline):
    model = Identity
    can_delete = False
    extra = 0
    form = QuotaOverrideForm
    fields = ("handle", "tier", "ov_inbox_mb", "ov_envelopes", "ov_contacts",
              "ov_sync_mb", "usage")
    readonly_fields = ("handle", "usage")
    verbose_name_plural = "📦 Quota and group (federated identity)"

    def usage(self, obj):
        from .quotas import effective_quota, current_usage
        if obj is None or obj.pk is None:
            return "—"
        lim, use = effective_quota(obj), current_usage(obj)
        return format_html(
            "<pre style='margin:0'>{}</pre>",
            "\n".join([
                f"in flight : {use.get('outbox_envelopes', 0)} envelopes / "
                f"{lim['max_inbox_envelopes'] or '∞'}   "
                f"{use.get('outbox_bytes', 0)} B / "
                f"{lim['max_inbox_bytes'] or '∞'}",
                f"received  : {use['inbox_envelopes']} envelopes / "
                f"{use['inbox_bytes']} B   (does not count against them)",
                f"contacts  : {use['contacts']} / {lim['max_contacts'] or '∞'}",
                f"sync      : {use['sync_bytes']} B / "
                f"{lim['max_sync_bytes'] or '∞'}"]))
    usage.short_description = "Current usage / limit"


class UserWithQuotaAdmin(UnfoldModelAdmin, DjangoUserAdmin):
    inlines = [IdentityInline]
    list_display = ("username", "handle_fed", "tier_fed", "is_staff",
                    "is_superuser", "last_login")

    @admin.display(description="handle")
    def handle_fed(self, obj):
        return getattr(getattr(obj, "identity", None), "handle", "—")

    @admin.display(description="group (tier)")
    def tier_fed(self, obj):
        return getattr(getattr(obj, "identity", None), "tier", None) or "—"


admin.site.unregister(User)
admin.site.register(User, UserWithQuotaAdmin)


@admin.register(EmailPin)
class EmailPinAdmin(UnfoldModelAdmin):
    list_display = ("user", "pin", "used", "created_at")

@admin.register(Contact)
class ContactAdmin(UnfoldModelAdmin):
    list_display = ("owner", "peer", "status", "trust_level")

@admin.register(Envelope)
class EnvelopeAdmin(UnfoldModelAdmin):
    list_display = ("resource_type", "sender", "recipient", "delivered",
                    "size_hint", "created_at")
    list_filter = ("resource_type", "delivered")
    list_select_related = ("sender", "recipient")
    exclude = ("ciphertext",)
    readonly_fields = ("size_hint",)
    show_full_result_count = False

    def get_queryset(self, request):
        return super().get_queryset(request).defer("ciphertext")


@admin.register(SpawnToken)
class SpawnTokenAdmin(UnfoldModelAdmin):
    list_display = ("owner", "label", "redeemed", "child", "expires_at", "created_at")
    list_filter = ("redeemed",)
    readonly_fields = ("token_hash", "created_at", "redeemed_at")


@admin.register(SyncSnapshot)
class SyncSnapshotAdmin(UnfoldModelAdmin):
    list_display = ("owner", "version", "size", "updated_at")
    list_select_related = ("owner",)
    exclude = ("ciphertext",)
    readonly_fields = ("owner", "version", "size", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).defer("ciphertext")


class NoticeForm(forms.ModelForm):
    to_handle = forms.CharField(
        required=False, label="To (handle)",
        help_text="Empty = EVERYONE (broadcast). Type one person's handle to "
                  "send it only to them.")

    class Meta:
        model = ServerNotice
        exclude = ("recipient",)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance and self.instance.pk and self.instance.recipient_id:
            self.fields["to_handle"].initial = self.instance.recipient.handle

    def clean_to_handle(self):
        h = (self.cleaned_data.get("to_handle") or "").strip()
        if not h:
            return None
        who = Identity.objects.filter(handle__iexact=h).first()
        if who:
            return who
        near = list(Identity.objects.filter(handle__icontains=h)
                    .values_list("handle", flat=True)[:8])
        raise forms.ValidationError(
            f"There is no '{h}'. " + (f"Did you mean: {', '.join(near)}?"
                                      if near else
                                      "Check the list of users."))

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.recipient = self.cleaned_data.get("to_handle")
        if commit:
            obj.save()
        return obj


@admin.register(ServerNotice)
class ServerNoticeAdmin(UnfoldModelAdmin):
    list_display = ("kind", "level", "recipient", "active", "delivered",
                    "short_body", "created_at")
    list_filter = ("kind", "level", "active", "delivered")
    form = NoticeForm
    search_fields = ("text", "code", "recipient__handle")
    readonly_fields = ("delivered", "delivered_at", "created_at")

    def short_body(self, obj):
        return (obj.text or f"{obj.code} {obj.params}")[:60]
    short_body.short_description = "Message"



@staff_member_required
def stats_view(request):
    from django.utils.html import escape as _esc
    if not getattr(request.user, "is_verified", lambda: False)():
        from django.shortcuts import redirect
        return redirect("/admin/")
    now = timezone.now()
    since = now - timedelta(days=14)
    _pend = {r["recipient"]: (r["n"], r["b"] or 0) for r in
             Envelope.objects.filter(delivered=False).values("recipient")
             .annotate(n=Count("id"), b=Sum("size_hint"))}
    _sent = {r["sender"]: r["n"] for r in
             Envelope.objects.values("sender").annotate(n=Count("id"))}
    _cont = {r["owner"]: r["n"] for r in
             Contact.objects.values("owner").annotate(n=Count("id"))}
    idents = list(Identity.objects.all().only(
        "id", "handle", "created_at", "last_seen"))
    for i in idents:
        i._pend, i._pbytes = _pend.get(i.id, (0, 0))
        i._ns = _sent.get(i.id, 0)
        i._nc = _cont.get(i.id, 0)
    alive, asleep, ghosts, abandoned = [], [], [], []
    for i in idents:
        if (i._nc == 0 and i._ns == 0
                and i.created_at < now - timedelta(hours=48)):
            abandoned.append(i)
        elif i.last_seen and i.last_seen >= now - timedelta(days=7):
            alive.append(i)
        elif i.last_seen and i.last_seen >= now - timedelta(days=30):
            asleep.append(i)
        else:
            ghosts.append(i)
    named_like_tests = [i for i in idents if _TESTY_RE.search(i.handle)]

    hours = [0] * 24
    for r in (Envelope.objects.filter(created_at__gte=since)
              .annotate(h=ExtractHour("created_at")).values("h")
              .annotate(n=Count("id"))):
        if r["h"] is not None:
            hours[int(r["h"])] += r["n"]
    days = {}
    for r in (Envelope.objects.filter(created_at__gte=since)
              .annotate(d=TruncDate("created_at")).values("d")
              .annotate(n=Count("id")).order_by("d")):
        if r["d"]:
            days[r["d"].isoformat()] = r["n"]
    types = (Envelope.objects.filter(created_at__gte=since)
             .values("resource_type").annotate(n=Count("id"))
             .order_by("-n")[:12])
    fullest_mailboxes = sorted(idents, key=lambda i: i._pend or 0,
                               reverse=True)[:8]

    def _row(cells, tag="td"):
        return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"

    def _kb(n):
        n = int(n or 0)
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024 or u == "GB":
                return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024.0

    def _bars(values, labels, unit=""):
        top = max(values) if values and max(values) else 0
        if not top:
            return (f"<div class='empty'>no data in this window "
                    f"— it appears here as soon as there is{unit}</div>")
        W, H, n = 720, 150, len(values)
        step = W / n
        bw = max(step - 6, 3)
        bars, ticks = [], []
        for i, v in enumerate(values):
            h = (H - 26) * v / top
            x = i * step + 3
            bars.append(
                f"<rect x='{x:.1f}' y='{H - 22 - h:.1f}' width='{bw:.1f}' "
                f"height='{max(h, 1.5):.1f}' rx='2' class='bar'>"
                f"<title>{labels[i]}: {v}</title></rect>")
            if n <= 24 or i % max(n // 12, 1) == 0:
                ticks.append(f"<text x='{x + bw / 2:.1f}' y='{H - 8}' "
                             f"class='tick'>{labels[i]}</text>")
        return (f"<svg viewBox='0 0 {W} {H}' class='chart'>"
                f"<line x1='0' y1='{H - 22}' x2='{W}' y2='{H - 22}' "
                f"class='axis'/>"
                f"<text x='2' y='12' class='max'>max {top}</text>"
                + "".join(bars) + "".join(ticks) + "</svg>")

    _days = sorted(days.items())
    charts = (_bars(hours, [f"{h:02d}" for h in range(24)], " (per hour)")
              + "<h2>📅 Daily volume (14 days)</h2>"
              + _bars([n for _d, n in _days],
                      [d[5:] for d, _n in _days], " (per day)"))

    _flight = Envelope.objects.filter(delivered=False).aggregate(
        n=Count("id"), b=Sum("size_hint"))
    html = f"""<meta charset='utf-8'><title>HOPLITA · stats</title>
<meta name='viewport' content='width=device-width,initial-scale=1'><style>
:root{{color-scheme:dark}}
body{{background:#0b1220;color:#cbd5e1;font:14px/1.5 system-ui,sans-serif;
margin:0;padding:28px 32px}}
h1{{color:#e2e8f0;font-size:20px;margin:0 0 4px}}
h2{{color:#93c5fd;font-size:13px;letter-spacing:1px;text-transform:uppercase;
margin:34px 0 10px;font-weight:600}}
.sub{{color:#64748b;font-size:12px;margin:0 0 22px}}
.kpis{{display:flex;flex-wrap:wrap;gap:12px}}
.kpi{{background:#111c30;border:1px solid #1f2937;border-radius:10px;
padding:12px 18px;min-width:120px}}
.kpi b{{display:block;font-size:26px;color:#e2e8f0;font-weight:700}}
.kpi span{{font-size:11px;color:#8296b1}}
table{{border-collapse:collapse;width:100%;max-width:760px;
background:#0f172a;border:1px solid #1f2937;border-radius:8px;overflow:hidden}}
td,th{{padding:6px 12px;text-align:left;border-bottom:1px solid #16233c}}
th{{color:#93c5fd;font-size:11px;letter-spacing:.5px;text-transform:uppercase;
background:#111c30}}
tr:last-child td{{border-bottom:none}}
td:nth-child(n+2){{font-variant-numeric:tabular-nums}}
.chart{{width:100%;max-width:760px;height:150px;display:block}}
.bar{{fill:#10b981}} .bar:hover{{fill:#34d399}}
.axis{{stroke:#1f2937}} .tick{{fill:#64748b;font-size:9px;text-anchor:middle}}
.max{{fill:#64748b;font-size:10px}}
.empty{{color:#64748b;font-size:12px;background:#0f172a;border:1px dashed
#1f2937;border-radius:8px;padding:18px;max-width:760px}}
a{{color:#93c5fd}}</style>
<h1>📊 {_esc(request.get_host())}</h1>
<p class='sub'>{now.strftime('%Y-%m-%d %H:%M')}
 · {timezone.get_current_timezone_name()} · 14-day window</p>
<div class='kpis'>
  <div class='kpi'><b>🟢 {len(alive)}</b><span>alive (7d)</span></div>
  <div class='kpi'><b>🟡 {len(asleep)}</b><span>asleep (7–30d)</span></div>
  <div class='kpi'><b>⚰ {len(ghosts)}</b><span>ghosts</span></div>
  <div class='kpi'><b>🧪 {len(abandoned)}</b><span>tried and forgot</span></div>
  <div class='kpi'><b>✉ {_flight['n'] or 0}</b><span>in flight ·
     {_kb(_flight['b'])}</span></div>
</div>
<h2>⏰ The hours they go all in</h2>
{charts}
<h2>🧪 Deletable (no contacts, no envelopes, &gt;48h)</h2>
<table><tr><th>handle</th><th>created</th><th>last seen</th></tr>
{''.join(_row([i.handle, i.created_at.date(), i.last_seen or '—'])
         for i in abandoned) or _row(['—', '', ''])}</table>
<p class='sub'>Named like a test (test/prueba/demo/tmp/borrar):
{len(named_like_tests)} —
{', '.join(i.handle for i in named_like_tests[:12]) or 'none'}</p>
<h2>📮 Fullest mailboxes</h2>
<table><tr><th>handle</th><th>undelivered</th><th>size</th></tr>
{''.join(_row([i.handle, i._pend or 0, _kb(i._pbytes)])
         for i in fullest_mailboxes if i._pend)
 or _row(['all delivered', '', ''])}</table>
<h2>🔀 Resource types</h2>
<table><tr><th>type</th><th>envelopes</th></tr>
{''.join(_row([t['resource_type'], t['n']]) for t in types)
        or _row(['—', 0])}</table>
<p class='sub'><a href='/admin/'>← admin</a></p>"""
    return HttpResponse(html)
