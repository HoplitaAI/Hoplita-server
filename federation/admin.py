from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import StackedInline as UnfoldStackedInline
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import display as ux_display
from unfold.widgets import (UnfoldAdminIntegerFieldWidget,
                            UnfoldAdminTextInputWidget)
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count
from django.shortcuts import redirect
from django.utils import timezone

from .models import (Identity, EmailPin, Contact, Envelope, SpawnToken,
                     SyncSnapshot, ServerNotice, QuotaTier, PublicFolder,
                     BannedEmail, RegistrationPolicy, InviteCode)
from .dashboard import BUCKET_LABEL, ago, bucket, human_bytes


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


@admin.register(RegistrationPolicy)
class RegistrationPolicyAdmin(UnfoldModelAdmin):
    fields = ("mode", "codes_view", "updated_at")
    readonly_fields = ("codes_view", "updated_at")

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse
        return redirect(reverse("admin:federation_registrationpolicy_change",
                                args=[RegistrationPolicy.current().pk]))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Invitation codes")
    def codes_view(self, obj):
        from django.urls import reverse
        n = InviteCode.usable().count()
        warn = (" — with the mode on invitation, NOBODY can sign up until "
                "there is one" if n == 0 and obj.mode == obj.INVITE else "")
        return format_html('{} usable{} · <a class="text-primary-600 underline" '
                           'href="{}">manage the codes</a>',
                           n, warn,
                           reverse("admin:federation_invitecode_changelist"))


@admin.register(InviteCode)
class InviteCodeAdmin(UnfoldModelAdmin):
    list_display = ("code_view", "note", "used", "expires_at", "status",
                    "who", "active")
    list_editable = ("active",)
    list_filter = ("active",)
    search_fields = ("code", "note", "identities__handle")
    fields = ("code", "note", "max_uses", "uses", "expires_at", "active",
              "who", "created_at")
    readonly_fields = ("uses", "who", "created_at")

    @admin.display(description="Code", ordering="code")
    def code_view(self, obj):
        return format_html('<code class="font-mono select-all">{}</code>',
                           obj.code)

    @admin.display(description="Used")
    def used(self, obj):
        return f"{obj.uses} / {obj.max_uses or '∞'}"

    @ux_display(description="State", label={"usable": "success",
                                            "used up": "info",
                                            "expired": "warning",
                                            "withdrawn": "danger"})
    def status(self, obj):
        return obj.state

    @admin.display(description="Signed up with it")
    def who(self, obj):
        if obj.pk is None:
            return "—"
        handles = list(obj.identities.values_list("handle", flat=True)[:20])
        more = obj.identities.count() - len(handles)
        return ", ".join(handles) + (f" (+{more})" if more > 0 else "") or "—"


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
        required=False, min_value=0, widget=UnfoldAdminIntegerFieldWidget,
        label="Mailbox in flight (MB)",
        help_text="Empty = whatever their group says. This is what they pay "
                  "for when SENDING.")
    ov_envelopes = forms.IntegerField(
        required=False, min_value=0, widget=UnfoldAdminIntegerFieldWidget,
        label="Envelopes in flight (count)")
    ov_contacts = forms.IntegerField(
        required=False, min_value=0, widget=UnfoldAdminIntegerFieldWidget,
        label="Contacts (count)")
    ov_sync_mb = forms.IntegerField(
        required=False, min_value=0, widget=UnfoldAdminIntegerFieldWidget,
        label="Sync snapshot (MB)")

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


def usage_meters(obj):
    from .quotas import effective_quota, current_usage
    if obj is None or obj.pk is None:
        return "—"
    lim, use = effective_quota(obj), current_usage(obj)
    rows = [
        ("Sent, not yet collected (charged)", use.get("outbox_bytes", 0),
         lim["max_inbox_bytes"], human_bytes),
        ("Envelopes sent, not yet collected", use.get("outbox_envelopes", 0),
         lim["max_inbox_envelopes"], str),
        ("Contacts", use["contacts"], lim["max_contacts"], str),
        ("Sync snapshot", use["sync_bytes"], lim["max_sync_bytes"],
         human_bytes),
        ("Children", use["children"], lim["max_children"], str),
        ("Waiting for them (free)", use["inbox_bytes"], 0, human_bytes),
    ]
    html = ['<div style="display:grid;grid-template-columns:auto 160px auto;'
            'gap:8px 14px;align-items:center;max-width:640px">']
    for label, used, limit, fmt in rows:
        pct = min(100, round(100 * used / limit)) if limit else 0
        color = ("#ef4444" if pct >= 95 else "#f59e0b" if pct >= 80
                 else "#10b981")
        bar = ('<div style="height:6px;border-radius:3px;overflow:hidden;'
               'background:rgba(148,163,184,.25)"><div style="height:100%;'
               f'width:{pct}%;background:{color}"></div></div>'
               if limit else '<div style="font-size:11px;opacity:.6">'
                             'no limit</div>')
        html.append(format_html(
            '<div>{}</div>{}<div style="text-align:right;'
            'font-variant-numeric:tabular-nums">{} / {}</div>',
            label, mark_safe(bar), fmt(used),
            fmt(limit) if limit else "∞"))
    html.append("</div>")
    return mark_safe("".join(str(h) for h in html))


@admin.register(Identity)
class IdentityAdmin(UnfoldModelAdmin):
    list_display = ("handle", "state", "tier", "online", "seen", "quota",
                    "in_flight", "pending")

    def get_queryset(self, request):
        from django.db.models import IntegerField, OuterRef, Subquery, Sum
        from django.db.models.functions import Coalesce
        from .models import Contact, Envelope

        def _count(qs, group):
            return Coalesce(Subquery(
                qs.order_by().values(group).annotate(c=Count("pk"))
                .values("c"), output_field=IntegerField()), 0)

        out = Envelope.objects.filter(sender=OuterRef("pk"), delivered=False)
        return super().get_queryset(request).select_related("tier").annotate(
            _pend=_count(Envelope.objects.filter(recipient=OuterRef("pk"),
                                                 delivered=False),
                         "recipient"),
            _nc=_count(Contact.objects.filter(owner=OuterRef("pk")), "owner"),
            _ns=_count(Envelope.objects.filter(sender=OuterRef("pk")),
                       "sender"),
            _out=Coalesce(Subquery(
                out.order_by().values("sender").annotate(b=Sum("size_hint"))
                .values("b"), output_field=IntegerField()), 0))

    @ux_display(description="State", label={"alive": "success",
                                            "asleep": "warning",
                                            "ghost": None, "tried": None})
    def state(self, obj):
        b = bucket(obj, timezone.now())
        return b, BUCKET_LABEL[b]

    @ux_display(description="Online", label={"on": "success"})
    def online(self, obj):
        return ("on", "online") if obj.is_online else ""

    @admin.display(description="Last seen", ordering="last_seen")
    def seen(self, obj):
        return ago(obj.last_seen)

    @admin.display(description="Waiting for them", ordering="_pend")
    def pending(self, obj):
        return obj._pend

    @admin.display(description="Sending limit")
    def quota(self, obj):
        from .quotas import effective_quota
        lim = effective_quota(obj)["max_inbox_bytes"]
        ov = (obj.quota_overrides or {}).get("max_inbox_bytes")
        return (human_bytes(lim) if lim else "∞") + (" ✎" if ov else "")

    @admin.display(description="Sent, in flight", ordering="_out")
    def in_flight(self, obj):
        return human_bytes(obj._out)
    list_filter = ("tier", "email_verified")
    list_editable = ("tier",)
    search_fields = ("handle",)
    form = QuotaOverrideForm
    fieldsets = (
        ("Who they are", {"fields": ("handle", "public_key", "fingerprint",
                                     "email_verified", "invite")}),
        ("Their quota", {
            "description": "The GROUP sets the limits for everyone in it. The "
                           "numbers below are ONLY for this person: empty "
                           "means the group rules.",
            "fields": ("tier", "ov_inbox_mb", "ov_envelopes", "ov_contacts",
                       "ov_sync_mb", "usage_view")}),
    )
    readonly_fields = ("handle", "public_key", "fingerprint", "invite",
                       "usage_view")

    @admin.display(description="Current usage / limit")
    def usage_view(self, obj):
        return usage_meters(obj)

class IdentityInline(UnfoldStackedInline):
    model = Identity
    can_delete = False
    extra = 0
    form = QuotaOverrideForm
    fields = ("handle", "tier", "ov_inbox_mb", "ov_envelopes", "ov_contacts",
              "ov_sync_mb", "usage")
    readonly_fields = ("handle", "usage")
    verbose_name_plural = "📦 Quota and group (federated identity)"

    @admin.display(description="Current usage / limit")
    def usage(self, obj):
        return usage_meters(obj)


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
    list_display = ("resource_type", "sender_h", "recipient_h", "delivered",
                    "size", "created_at")
    list_filter = ("resource_type", "delivered")
    list_select_related = ("sender", "recipient")
    exclude = ("ciphertext",)
    readonly_fields = ("size_hint",)
    show_full_result_count = False

    def get_queryset(self, request):
        return super().get_queryset(request).defer("ciphertext")

    @admin.display(description="From", ordering="sender__handle")
    def sender_h(self, obj):
        return obj.sender.handle

    @admin.display(description="To", ordering="recipient__handle")
    def recipient_h(self, obj):
        return obj.recipient.handle

    @admin.display(description="Size", ordering="size_hint")
    def size(self, obj):
        return human_bytes(obj.size_hint)


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
        widget=UnfoldAdminTextInputWidget,
        help_text="Empty = EVERYONE (broadcast). Type one person's handle to "
                  "send it only to them.")

    class Meta:
        model = ServerNotice
        exclude = ("recipient",)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance and self.instance.pk and self.instance.recipient_id:
            self.fields["to_handle"].initial = self.instance.recipient.handle
        for name, rows in (("text", 4), ("params", 2)):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = rows

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
    fieldsets = (
        ("Message", {"fields": ("text", "to_handle", "level", "kind",
                                "active", "expires_at")}),
        ("Only for notices the client translates", {
            "description": "Leave both empty for a plain message: the text "
                           "above is shown as written.",
            "fields": ("code", "params")}),
        ("Delivery", {"fields": ("delivered", "delivered_at", "created_at")}),
    )

    def get_fieldsets(self, request, obj=None):
        return self.fieldsets if obj else self.fieldsets[:2]

    def short_body(self, obj):
        return (obj.text or f"{obj.code} {obj.params}")[:60]
    short_body.short_description = "Message"


def stats_view(request):
    return redirect("admin:index")
