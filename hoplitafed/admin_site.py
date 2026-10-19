from django import forms
from django.contrib import admin
from django_otp import devices_for_user
from django_otp.admin import OTPAdminAuthenticationForm, OTPAdminSite
from unfold.sites import UnfoldAdminSite
from unfold.widgets import BASE_INPUT_CLASSES


class _CodeInput(forms.TextInput):

    def format_value(self, value):
        return None


class HoplitaLoginForm(OTPAdminAuthenticationForm):
    otp_token = forms.CharField(
        required=False, label="Authenticator code",
        widget=_CodeInput(attrs={
            "autocomplete": "one-time-code", "inputmode": "numeric",
            "pattern": r"[0-9 ]*", "maxlength": "8", "placeholder": "123 456",
            "class": " ".join(BASE_INPUT_CLASSES)
            + " text-center tracking-[0.4em] font-mono text-lg"}))
    otp_device = forms.CharField(required=False, widget=forms.HiddenInput)
    otp_challenge = forms.CharField(required=False, widget=forms.HiddenInput)

    otp_error_messages = dict(
        OTPAdminAuthenticationForm.otp_error_messages,
        token_required="Type the 6-digit code your authenticator app shows "
                       "for this server.",
        invalid_token="That code did not match. Codes change every 30 "
                      "seconds: type the one showing now.")

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        cls = " ".join(BASE_INPUT_CLASSES)
        self.fields["username"].widget.attrs.update(
            {"class": cls, "autofocus": "", "autocomplete": "username"})
        self.fields["password"].widget.attrs.update(
            {"class": cls, "autocomplete": "current-password"})
        if self.is_bound:
            self.fields["username"].widget.attrs.pop("autofocus", None)
            self.fields["password"].widget.attrs["autofocus"] = ""

    def _verify_token(self, user, token, device=None):
        if device is not None:
            allowed, extra = device.verify_is_allowed()
            until = extra.get("locked_until") if not allowed else None
            if until is not None:
                from math import ceil
                from django.utils import timezone
                wait = max(1, ceil((until - timezone.now()).total_seconds()))
                raise forms.ValidationError(
                    f"Too many wrong codes in a row. Wait {wait} "
                    f"second{'s' if wait != 1 else ''}, then type the code "
                    f"showing now.", code="throttled")
        try:
            return super()._verify_token(user, token, device)
        except forms.ValidationError:
            if device is not None and self._was_just_used(device, token):
                raise forms.ValidationError(
                    "That code was just used to sign in. Wait for the next "
                    "one (a few seconds) and type it.", code="reused")
            raise

    @staticmethod
    def _was_just_used(device, token):
        import time
        from django_otp.oath import TOTP
        try:
            totp = TOTP(device.bin_key, device.step, device.t0,
                        device.digits, device.drift)
            totp.time = time.time()
            return (device.last_t >= 0
                    and totp.verify(int(token), device.tolerance, 0)
                    and totp.t() - device.tolerance <= device.last_t)
        except Exception:
            return False

    def _chosen_device(self, user):
        device = super()._chosen_device(user)
        if device is None:
            devices = list(devices_for_user(user, for_verify=True))
            if len(devices) == 1:
                device = devices[0]
        return device

    def clean_otp_token(self):
        return "".join((self.cleaned_data.get("otp_token") or "").split())

    def _update_form(self, user):
        pass


class HoplitaAdminSite(OTPAdminSite, UnfoldAdminSite):

    login_template = "hoplita/login.html"
    index_template = "hoplita/dashboard.html"
    index_title = "Dashboard"

    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        if app_label:
            return apps
        return apps + [{
            "name": "Operation",
            "app_label": "operacion",
            "app_url": "/admin/",
            "has_module_perms": True,
            "models": [{
                "name": "Dashboard",
                "object_name": "dashboard",
                "admin_url": "/admin/",
                "add_url": None,
                "view_only": True,
                "perms": {"add": False, "change": False,
                          "delete": False, "view": True},
            }],
        }]


def install():
    admin.site.__class__ = HoplitaAdminSite
    admin.site.login_form = HoplitaLoginForm
    admin.site.login_template = HoplitaAdminSite.login_template
    admin.site.index_template = HoplitaAdminSite.index_template
    admin.site.index_title = HoplitaAdminSite.index_title
