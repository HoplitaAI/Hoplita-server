
import secrets
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class QuotaTier(models.Model):
    class Meta:
        verbose_name = "group quota"
        verbose_name_plural = "📦 Quotas by group"

    name = models.CharField(max_length=40, unique=True, db_index=True)
    max_sync_bytes = models.BigIntegerField(default=0, help_text="Max size of the sync snapshot (0=unlimited)")
    max_inbox_envelopes = models.IntegerField(default=0, help_text="Undelivered envelopes in the mailbox (0=unlimited)")
    max_inbox_bytes = models.BigIntegerField(default=0, help_text="Undelivered bytes in the mailbox (0=unlimited)")
    max_contacts = models.IntegerField(default=0, help_text="Contacts (0=unlimited)")
    max_children = models.IntegerField(default=0, help_text="Enrolled children (0=unlimited)")
    max_requests_per_min = models.IntegerField(default=0, help_text="API requests per minute (0=unlimited)")
    is_default = models.BooleanField(default=False, help_text="Tier applied to anyone without one")
    created_at = models.DateTimeField(auto_now_add=True)

    FIELDS = ("max_sync_bytes", "max_inbox_envelopes", "max_inbox_bytes",
              "max_contacts", "max_children", "max_requests_per_min")

    @classmethod
    def get_default(cls):
        return (cls.objects.filter(is_default=True).order_by("id").first()
                or cls.objects.order_by("id").first())

    def save(self, *args, **kwargs):
        from django.db import transaction
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_default:
                type(self).objects.exclude(pk=self.pk).filter(
                    is_default=True).update(is_default=False)

    def as_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}

    def __str__(self):
        return f"QuotaTier({self.name})"


class Identity(models.Model):
    class Meta:
        verbose_name = "user (federated identity)"
        verbose_name_plural = "👤 Users and their quotas"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="identity")
    handle = models.CharField(max_length=255, unique=True, db_index=True)
    tier = models.ForeignKey("QuotaTier", on_delete=models.SET_NULL, null=True,
                             blank=True, related_name="members")
    quota_overrides = models.JSONField(default=dict, blank=True,
                                       help_text='e.g. {"max_sync_bytes": 5000000}')
    public_key = models.TextField(
        help_text="Curve25519 public key, base64")
    sign_pub = models.TextField(blank=True, default="",
        help_text="Ed25519 signing public key, base64")
    fingerprint = models.CharField(max_length=64, blank=True)
    email_verified = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    ws_online = models.BooleanField(default=False)
    ws_conns = models.IntegerField(default=0)
    invite = models.ForeignKey("InviteCode", on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="identities")
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_online(self):
        if not self.ws_online or not self.last_seen:
            return False
        from django.utils import timezone
        return (timezone.now() - self.last_seen).total_seconds() < 180

    def touch(self):
        self.last_seen = timezone.now()
        self.save(update_fields=["last_seen"])

    def __str__(self):
        return f"{self.handle} ({'online' if self.is_online else 'offline'})"


class EmailPin(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_pins")
    pin = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    PIN_TTL_MINUTES = 15

    @staticmethod
    def generate_pin():
        return f"{secrets.randbelow(1_000_000):06d}"

    @property
    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > self.PIN_TTL_MINUTES * 60

    def __str__(self):
        return (f"PIN for {self.user.username} "
                f"({'used' if self.used else 'active'})")


class Contact(models.Model):

    PENDING = "pending"
    PENDING_INCOMING = "pending_incoming"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    STATUS_CHOICES = [(PENDING, "Pending (sent)"),
                      (PENDING_INCOMING, "Pending (received, yours to accept)"),
                      (ACCEPTED, "Accepted"), (BLOCKED, "Blocked")]

    TRUST_OPEN = "open"
    TRUST_ACCEPT = "accept"
    TRUST_ANALYZE = "analyze"
    TRUST_CHOICES = [
        (TRUST_OPEN, "Free rein"),
        (TRUST_ACCEPT, "With acceptance"),
        (TRUST_ANALYZE, "Acceptance + analysis"),
    ]

    owner = models.ForeignKey(Identity, on_delete=models.CASCADE, related_name="contacts")
    peer = models.ForeignKey(Identity, on_delete=models.CASCADE, related_name="contact_of")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    trust_level = models.CharField(max_length=10, choices=TRUST_CHOICES, default=TRUST_ACCEPT)
    permissions = models.JSONField(default=dict, blank=True)
    attestation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("owner", "peer")

    def __str__(self):
        return f"{self.owner.handle} -> {self.peer.handle} [{self.status}/{self.trust_level}]"


class Envelope(models.Model):

    sender = models.ForeignKey(Identity, on_delete=models.CASCADE, related_name="sent_envelopes")
    recipient = models.ForeignKey(Identity, on_delete=models.CASCADE, related_name="inbox", db_index=True)
    resource_type = models.CharField(max_length=40, db_index=True)
    ciphertext = models.TextField()
    size_hint = models.IntegerField(default=0, help_text="len(ciphertext), for mailbox quota accounting")
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    delivered = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.resource_type}] {self.sender.handle} -> {self.recipient.handle}"


class SharedFolder(models.Model):
    PENDING = "pending"
    ACCEPTED = "accepted"
    STATUS_CHOICES = [(PENDING, "pending"), (ACCEPTED, "accepted")]
    KIND_FOLDER = "folder"
    KIND_PROJECT = "project"
    KIND_CHOICES = [(KIND_FOLDER, "folder"), (KIND_PROJECT, "project")]

    owner = models.ForeignKey(Identity, on_delete=models.CASCADE, related_name="shared_folders")
    peer = models.ForeignKey(Identity, on_delete=models.CASCADE, related_name="folders_shared_with_me")
    folder_id = models.CharField(max_length=64, db_index=True)
    folder_name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_FOLDER)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    readonly = models.BooleanField(default=False)
    grant = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("owner", "peer", "folder_id")]
        ordering = ["created_at"]

    def __str__(self):
        ro = " [ro]" if self.readonly else ""
        return f"[{self.status}]{ro} {self.folder_name} ({self.owner.handle} <-> {self.peer.handle})"


class PublicFolder(models.Model):
    owner = models.ForeignKey(Identity, on_delete=models.CASCADE,
                              related_name="public_folders")
    folder_id = models.CharField(max_length=64, db_index=True)
    folder_name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=SharedFolder.KIND_CHOICES,
                            default=SharedFolder.KIND_FOLDER)
    attestation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("owner", "folder_id")]
        ordering = ["created_at"]

    def subscriber_count(self):
        return SharedFolder.objects.filter(owner=self.owner,
                                           folder_id=self.folder_id,
                                           readonly=True).count()

    def __str__(self):
        return f"public {self.folder_name} ({self.owner.handle})"


class BannedEmail(models.Model):
    email = models.EmailField(unique=True, db_index=True)
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def is_banned(email):
        e = (email or "").strip().lower()
        return bool(e) and BannedEmail.objects.filter(email__iexact=e).exists()

    def __str__(self):
        return f"banned {self.email}" + (f" ({self.reason})" if self.reason else "")


class RegistrationPolicy(models.Model):
    CLOSED = "closed"; INVITE = "invite"; OPEN = "open"
    MODE_CHOICES = [
        (CLOSED, "Closed — nobody can sign up"),
        (INVITE, "Invitation — only with a usable code"),
        (OPEN, "Open — anyone can sign up"),
    ]
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=INVITE,
        help_text="Open on a public domain means anyone who finds it creates "
                  "identities and spends quota. It also needs working mail: "
                  "the verification PIN goes by email.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "sign-up policy"
        verbose_name_plural = "🚪 Sign-up policy"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def __str__(self):
        return dict(self.MODE_CHOICES)[self.mode]


def _new_invite_code():
    return secrets.token_urlsafe(12)


class InviteCode(models.Model):
    code = models.CharField(max_length=64, unique=True, default=_new_invite_code,
        help_text="What the person types. A random one is proposed; any text works.")
    note = models.CharField(max_length=200, blank=True,
        help_text="Who it is for, so you recognise it later.")
    max_uses = models.PositiveIntegerField(default=1,
        help_text="How many accounts it can create. 0 = unlimited.")
    uses = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True,
        help_text="Optional: after this instant it no longer works.")
    active = models.BooleanField(default=True,
        help_text="Untick to withdraw it. Accounts already created stay.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "invitation code"
        verbose_name_plural = "🎟 Invitation codes"

    @property
    def state(self):
        if not self.active:
            return "withdrawn"
        if self.expires_at and timezone.now() >= self.expires_at:
            return "expired"
        if self.max_uses and self.uses >= self.max_uses:
            return "used up"
        return "usable"

    @classmethod
    def usable(cls):
        now = timezone.now()
        return (cls.objects.filter(active=True)
                .filter(models.Q(expires_at__isnull=True)
                        | models.Q(expires_at__gt=now))
                .filter(models.Q(max_uses=0)
                        | models.Q(uses__lt=models.F("max_uses"))))

    def spend(self):
        return bool(InviteCode.usable().filter(pk=self.pk)
                    .update(uses=models.F("uses") + 1))

    def __str__(self):
        return f"{self.code} ({self.note or 'no note'}) [{self.state}]"


class SpawnToken(models.Model):
    import hashlib as _hashlib

    owner = models.ForeignKey("Identity", on_delete=models.CASCADE,
                              related_name="spawn_tokens")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    label = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    redeemed = models.BooleanField(default=False)
    redeemed_at = models.DateTimeField(null=True, blank=True)
    child = models.ForeignKey("Identity", on_delete=models.SET_NULL, null=True,
                              blank=True, related_name="spawned_from")

    TOKEN_TTL_MINUTES = 60

    @staticmethod
    def hash_token(raw):
        import hashlib
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def new_raw_token():
        return secrets.token_urlsafe(32)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_usable(self):
        return (not self.redeemed) and (not self.is_expired)

    def __str__(self):
        state = ("used" if self.redeemed
                 else ("expired" if self.is_expired else "active"))
        return f"SpawnToken({self.owner.handle} → {self.label or '?'}) [{state}]"


class SyncSnapshot(models.Model):
    owner = models.OneToOneField(Identity, on_delete=models.CASCADE,
                                 related_name="sync_snapshot")
    version = models.IntegerField(default=0)
    ciphertext = models.TextField()
    size = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SyncSnapshot({self.owner.handle}) v{self.version} ({self.size}B)"


class ServerNotice(models.Model):
    MOTD = "motd"; QUOTA = "quota"; SYSTEM = "system"; ALERT = "alert"
    KIND_CHOICES = [(MOTD, "MOTD (broadcast)"), (QUOTA, "Quota"),
                    (SYSTEM, "System"), (ALERT, "Alert")]
    INFO = "info"; WARN = "warn"; CRITICAL = "critical"
    LEVEL_CHOICES = [(INFO, "Info"), (WARN, "Warning"), (CRITICAL, "Critical")]

    recipient = models.ForeignKey("Identity", null=True, blank=True,
        on_delete=models.CASCADE, related_name="notices",
        help_text="Target user. Leave EMPTY to broadcast to everyone (MOTD).")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=MOTD,
        db_index=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=INFO)
    text = models.TextField(blank=True, default="",
        help_text="Free operator text (MOTD). For system notices use code+params.")
    code = models.CharField(max_length=64, blank=True, default="",
        help_text="Machine code the client localizes via t() (e.g. quota_exceeded).")
    params = models.JSONField(default=dict, blank=True,
        help_text="Params for the code's translation, e.g. {resource,used,limit}.")
    active = models.BooleanField(default=True, db_index=True)
    delivered = models.BooleanField(default=False, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True,
        help_text="Optional: after this instant the notice is no longer served.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "MOTD / server notice"
        verbose_name_plural = "📢 MOTD and notices"

    def as_payload(self):
        return {"id": self.id, "kind": self.kind, "level": self.level,
                "text": self.text, "code": self.code, "params": self.params,
                "created_at": self.created_at.isoformat()}

    def __str__(self):
        who = self.recipient.handle if self.recipient_id else "ALL"
        body = self.text or f"{self.code} {self.params}"
        return f"Notice[{self.kind}/{self.level}→{who}{'' if self.active else ' (off)'}] {body[:40]}"


class LoginChallenge(models.Model):
    identity = models.ForeignKey("Identity", on_delete=models.CASCADE,
                                 related_name="login_challenges")
    nonce = models.CharField(max_length=128, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    @property
    def is_usable(self):
        return (not self.used) and (timezone.now() <= self.expires_at)

    def __str__(self):
        return (f"LoginChallenge({self.identity.handle}) "
                f"{'used' if self.used else 'active'}")
