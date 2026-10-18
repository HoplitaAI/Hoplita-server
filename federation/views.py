
import logging
import os
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token

_log = logging.getLogger(__name__)


def _send_pin(to_email, handle, pin):
    if not to_email:
        return False
    try:
        sent = send_mail(
            subject=f"HOPLITA — verification code for {handle}",
            message=(f"Your code is {pin}\n\n"
                     f"It expires in {EmailPin.PIN_TTL_MINUTES} minutes. "
                     f"If you did not ask for it, ignore this message: without "
                     f"the code nothing was activated."),
            from_email=None,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return bool(sent)
    except Exception as e:
        _log.error("could not send the verification email to %s: %s",
                   handle, e)
        return False

from .models import (Identity, EmailPin, Contact, Envelope,
                     SpawnToken, LoginChallenge, QuotaTier)
from . import services
from .push import notify_user, reset_live



@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    data = request.data

    from .models import BannedEmail
    if BannedEmail.is_banned(data.get("email")):
        return Response({"error": "banned",
                         "message": "This email is banned on this server."},
                        status=403)

    if not getattr(settings, "FED_OPEN_REGISTRATION", False):
        codes = [c.strip() for c in
                 os.environ.get("HOPLITA_INVITE_CODES", "").split(",") if c.strip()]
        if not codes:
            return Response(
                {"error": "registration_closed",
                 "message": "This server is not accepting new accounts."}, status=403)
        given = str(data.get("invite") or "").strip()
        if not any(secrets.compare_digest(given, c) for c in codes):
            return Response(
                {"error": "invite_required",
                 "message": "You need an invitation code to register on this "
                            "server."}, status=403)

    required = ["handle", "email", "public_key"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return Response({"error": f"Missing fields: {', '.join(missing)}"}, status=400)

    handle = data["handle"].strip().lower()
    if Identity.objects.filter(handle=handle).exists():
        return Response({"error": "That handle already exists"}, status=409)
    if User.objects.filter(username=handle).exists():
        return Response({"error": "That user already exists"}, status=409)

    try:
        user = User.objects.create_user(username=handle, email=data["email"])
        pwd = data.get("password")
        if pwd:
            user.set_password(pwd)
        else:
            user.set_unusable_password()
        user.save()
    except IntegrityError:
        return Response({"error": "Could not create the user"}, status=409)

    Identity.objects.create(
        user=user, handle=handle,
        public_key=data["public_key"],
        sign_pub=str(data.get("sign_pub") or "")[:128],
        fingerprint=data.get("fingerprint", ""),
        tier=QuotaTier.get_default(),
    )
    pin_obj = EmailPin.objects.create(user=user, pin=EmailPin.generate_pin())

    email_sent = _send_pin(user.email, handle, pin_obj.pin)

    if getattr(settings, "FED_EXPOSE_DEV_PIN", False):
        _log.warning("[DEV] verification PIN for %s: %s", handle, pin_obj.pin)

    body = {
        "ok": True,
        "handle": handle,
        "email_sent": email_sent,
        "message": ("Account created. Verify with the PIN sent to your email."
                    if email_sent else
                    "Account created, but the verification email could NOT be "
                    "sent. Ask the server admin for your PIN — you cannot log "
                    "in until the account is verified."),
    }
    if getattr(settings, "FED_EXPOSE_DEV_PIN", False):
        body["dev_pin"] = pin_obj.pin
    return Response(body, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
def verify_email(request):
    handle = (request.data.get("handle") or "").strip().lower()
    pin = (request.data.get("pin") or "").strip()
    try:
        identity = Identity.objects.get(handle=handle)
    except Identity.DoesNotExist:
        return Response({"error": "Handle not found"}, status=404)

    pin_obj = (EmailPin.objects.filter(user=identity.user, pin=pin, used=False)
               .order_by("-created_at").first())
    if not pin_obj:
        return Response({"error": "Wrong PIN"}, status=400)
    if pin_obj.is_expired:
        return Response({"error": "PIN expired"}, status=400)

    EmailPin.objects.filter(user=identity.user).delete()
    identity.email_verified = True
    identity.save(update_fields=["email_verified"])
    return Response({"ok": True, "message": "Email verified"})


@api_view(["POST"])
@permission_classes([AllowAny])
def login_challenge(request):
    import os as _os, base64 as _b64, sys as _sys
    from datetime import timedelta
    handle = (request.data.get("handle") or "").strip().lower()
    try:
        identity = Identity.objects.get(handle=handle)
    except Identity.DoesNotExist:
        return Response({"error": "Handle not found"}, status=404)
    if not identity.email_verified:
        return Response({"error": "Email not verified"}, status=403)
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    try:
        import hoplita_crypto as crypto
    except ImportError as e:
        return Response({"error": f"The server is missing a dependency: {e}. "
                         "Install requirements.txt (pynacl included)."}, status=500)
    nonce = _b64.urlsafe_b64encode(_os.urandom(32)).decode("utf-8").rstrip("=")
    try:
        blob = crypto.seal_for(identity.public_key, {"nonce": nonce})
    except Exception as e:
        return Response({"error": f"Could not encrypt the challenge: {e}"}, status=500)
    LoginChallenge.objects.filter(identity=identity, used=False).update(used=True)
    LoginChallenge.objects.create(
        identity=identity, nonce=nonce,
        expires_at=timezone.now() + timedelta(minutes=2))
    return Response({"ok": True, "challenge": blob})


@api_view(["POST"])
@permission_classes([AllowAny])
def login_verify(request):
    handle = (request.data.get("handle") or "").strip().lower()
    nonce = request.data.get("nonce") or ""
    try:
        identity = Identity.objects.get(handle=handle)
    except Identity.DoesNotExist:
        return Response({"error": "Handle not found"}, status=404)
    ch = (LoginChallenge.objects
          .filter(identity=identity, nonce=nonce, used=False)
          .order_by("-created_at").first())
    if not ch or not ch.is_usable:
        return Response({"error": "Challenge invalid or expired"}, status=401)
    LoginChallenge.objects.filter(identity=identity).filter(
        Q(pk=ch.pk) | Q(used=True) | Q(expires_at__lt=timezone.now())).delete()
    token, _ = Token.objects.get_or_create(user=identity.user)
    identity.touch()
    return Response({"ok": True, "token": token.key, "handle": handle,
                     "public_key": identity.public_key})


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    handle = (request.data.get("handle") or "").strip().lower()
    password = request.data.get("password") or ""
    from django.contrib.auth import authenticate
    user = authenticate(username=handle, password=password)
    if not user:
        return Response({"error": "Invalid credentials"}, status=401)
    identity = getattr(user, "identity", None)
    if identity and not identity.email_verified:
        return Response({"error": "Email not verified"}, status=403)

    token, _ = Token.objects.get_or_create(user=user)
    if identity:
        identity.touch()
    return Response({
        "ok": True, "token": token.key, "handle": handle,
        "public_key": identity.public_key if identity else None,
    })



@api_view(["POST"])
@permission_classes([IsAuthenticated])
def heartbeat(request):
    identity = request.user.identity
    identity.touch()
    return Response({"ok": True, "last_seen": identity.last_seen.isoformat()})



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_public_key(request, handle):
    payload, status = services.svc_get_key(request.user.identity,
                                           {"handle": handle})
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_contact(request):
    payload, status = services.svc_add_contact(request.user.identity,
                                               request.data)
    if status == 201 and payload.get("peer_id"):
        notify_user(payload["peer_id"], "contact.update",
                    {"from": request.user.identity.handle,
                     "status": payload.get("status")})
    payload.pop("peer_id", None)
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def accept_contact(request):
    payload, status = services.svc_accept_contact(request.user.identity,
                                                  request.data)
    if status == 200 and payload.get("peer_id"):
        notify_user(payload["peer_id"], "contact.update",
                    {"from": request.user.identity.handle,
                     "status": "accepted"})
    payload.pop("peer_id", None)
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def publish_sign_key(request):
    payload, status = services.svc_publish_sign_key(request.user.identity,
                                                    request.data)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attest_contact(request):
    payload, status = services.svc_attest_contact(request.user.identity,
                                                  request.data)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def grant_folder(request):
    payload, status = services.svc_grant_folder(request.user.identity,
                                                request.data)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def deregister(request):
    me = request.user.identity
    me_user = me.user

    child_ids = list(SpawnToken.objects.filter(owner=me, child__isnull=False)
                     .values_list("child_id", flat=True))
    revoked_children = 0
    for cid in set(child_ids):
        try:
            child = Identity.objects.get(id=cid)
            cu = child.user
            Token.objects.filter(user=cu).delete()
            Contact.objects.filter(Q(owner=child) | Q(peer=child)).delete()
            Envelope.objects.filter(Q(sender=child) | Q(recipient=child)).delete()
            child.delete()
            cu.delete()
            revoked_children += 1
        except Identity.DoesNotExist:
            pass

    Contact.objects.filter(Q(owner=me) | Q(peer=me)).delete()
    Envelope.objects.filter(Q(sender=me) | Q(recipient=me)).delete()
    SpawnToken.objects.filter(owner=me).delete()

    Token.objects.filter(user=me_user).delete()
    handle = me.handle
    me.delete()
    me_user.delete()
    return Response({"ok": True, "left": handle,
                     "revoked_children": revoked_children})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def remove_contact(request):
    payload, status = services.svc_remove_contact(request.user.identity,
                                                  request.data)
    if status == 200 and payload.get("peer_id"):
        notify_user(payload["peer_id"], "contact.update",
                    {"from": request.user.identity.handle,
                     "status": "removed"})
        reset_live(request.user.identity.id, payload["peer_id"])
    payload.pop("peer_id", None)
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def revoke_child(request):
    me = request.user.identity
    child_handle = (request.data.get("handle") or "").strip().lower()
    try:
        child = Identity.objects.get(handle=child_handle)
    except Identity.DoesNotExist:
        return Response({"error": "Child not found"}, status=404)

    if not SpawnToken.objects.filter(owner=me, child=child).exists():
        return Response({"error": f"'{child_handle}' is not one of your children"}, status=403)

    child_user = child.user
    Token.objects.filter(user=child_user).delete()
    Contact.objects.filter(Q(owner=me, peer=child) | Q(owner=child, peer=me)).delete()
    Envelope.objects.filter(
        Q(sender=me, recipient=child) | Q(sender=child, recipient=me)).delete()
    SpawnToken.objects.filter(owner=me, child=child).update(child=None)
    child.delete()
    child_user.delete()
    return Response({"ok": True, "revoked": child_handle})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_contacts(request):
    payload, status = services.svc_list_contacts(request.user.identity, {})
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def set_permissions(request):
    payload, status = services.svc_set_permissions(request.user.identity,
                                                   request.data)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_spawn_token(request):
    me = request.user.identity
    from . import quotas
    err = quotas.check_quota(me, "children", incoming=1)
    if err:
        return Response(err[0], status=err[1])
    label = (request.data.get("label") or "").strip()[:120]
    raw = SpawnToken.new_raw_token()
    tok = SpawnToken.objects.create(
        owner=me, token_hash=SpawnToken.hash_token(raw), label=label,
        expires_at=timezone.now() + timezone.timedelta(
            minutes=SpawnToken.TOKEN_TTL_MINUTES))
    return Response({
        "ok": True,
        "token": raw,
        "label": label,
        "expires_at": tok.expires_at.isoformat(),
        "spawn_id": tok.id,
    }, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
def redeem_spawn_token(request):
    token_raw = request.data.get("token") or ""
    handle = (request.data.get("handle") or "").strip().lower()
    public_key = request.data.get("public_key") or ""
    if not (token_raw and handle and public_key):
        return Response({"error": "Missing fields (token, handle, public_key)"}, status=400)

    try:
        tok = SpawnToken.objects.get(token_hash=SpawnToken.hash_token(token_raw))
    except SpawnToken.DoesNotExist:
        return Response({"error": "Invalid token"}, status=401)
    if not tok.is_usable:
        reason = "already used" if tok.redeemed else "expired"
        return Response({"error": f"Token {reason}"}, status=403)

    if Identity.objects.filter(handle=handle).exists() or \
       User.objects.filter(username=handle).exists():
        return Response({"error": "That handle already exists"}, status=409)

    owner = tok.owner
    user = User.objects.create_user(username=handle, email="")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    child = Identity.objects.create(
        user=user, handle=handle, public_key=public_key,
        fingerprint=request.data.get("fingerprint", ""),
        email_verified=True,
        tier=QuotaTier.get_default(),
    )

    Contact.objects.update_or_create(
        owner=owner, peer=child,
        defaults={"status": Contact.ACCEPTED, "trust_level": Contact.TRUST_OPEN})
    Contact.objects.update_or_create(
        owner=child, peer=owner,
        defaults={"status": Contact.ACCEPTED, "trust_level": Contact.TRUST_OPEN})

    tok.redeemed = True
    tok.redeemed_at = timezone.now()
    tok.child = child
    tok.save(update_fields=["redeemed", "redeemed_at", "child"])

    child.touch()
    session_token, _ = Token.objects.get_or_create(user=user)
    return Response({
        "ok": True,
        "handle": handle,
        "token": session_token.key,
        "owner": owner.handle,
    }, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_children(request):
    me = request.user.identity
    children = Identity.objects.filter(spawned_from__owner=me).distinct()
    return Response({"ok": True, "children": [
        {"handle": c.handle, "public_key": c.public_key,
         "online": c.is_online,
         "last_seen": c.last_seen.isoformat() if c.last_seen else None}
        for c in children]})



@api_view(["POST"])
@permission_classes([IsAuthenticated])
def send_envelope(request):
    payload, status = services.svc_send_envelope(request.user.identity,
                                                 request.data)
    if status == 201:
        notify_user(payload.pop("recipient_id"), "envelope.new",
                    payload.pop("notice"))
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ack_envelopes(request):
    payload, status = services.svc_ack_envelopes(request.user.identity,
                                                 request.data)
    return Response(payload, status=status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fetch_inbox(request):
    payload, status = services.svc_fetch_inbox(
        request.user.identity, dict(request.query_params.items()))
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def withdraw_envelopes(request):
    payload, status = services.svc_withdraw_envelopes(request.user.identity,
                                                      request.data)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def share_folder(request):
    payload, status = services.svc_share_folder(request.user.identity,
                                                request.data)
    if status == 201:
        notify_user(payload.pop("peer_id"), "folder.shared",
                    {"from": request.user.identity.handle,
                     "folder_name": payload.get("folder_name"),
                     "kind": payload.get("kind")})
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def accept_folder(request):
    payload, status = services.svc_accept_folder(request.user.identity,
                                                 request.data)
    if status == 200:
        notify_user(payload.pop("peer_id"), "folder.accepted",
                    {"by": request.user.identity.handle,
                     "folder_name": payload.get("folder_name"),
                     "kind": payload.get("kind")})
    return Response(payload, status=status)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def unshare_folder(request):
    payload, status = services.svc_unshare_folder(request.user.identity,
                                                  request.data)
    if status == 200:
        for pid in payload.pop("peer_ids", []):
            notify_user(pid, "folder.unshared",
                        {"by": request.user.identity.handle,
                         "folder_name": payload.get("folder_name"),
                         "kind": payload.get("kind")})
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def publish_folder(request):
    payload, status = services.svc_publish_folder(request.user.identity,
                                                  request.data)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def unpublish_folder(request):
    payload, status = services.svc_unpublish_folder(request.user.identity,
                                                    request.data)
    return Response(payload, status=status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def public_folders(request):
    payload, status = services.svc_public_folders(
        request.user.identity, dict(request.query_params.items()))
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def subscribe_folder(request):
    payload, status = services.svc_subscribe_folder(request.user.identity,
                                                    request.data)
    if status == 201:
        notify_user(payload.pop("owner_id"), "folder.subscribed",
                    {"by": request.user.identity.handle,
                     "folder_id": payload.get("folder_id"),
                     "folder_name": payload.get("folder_name"),
                     "subscribers": payload.get("subscribers")})
    else:
        payload.pop("owner_id", None)
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def unsubscribe_folder(request):
    payload, status = services.svc_unsubscribe_folder(request.user.identity,
                                                      request.data)
    return Response(payload, status=status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_folders(request):
    payload, status = services.svc_list_folders(request.user.identity, {})
    return Response(payload, status=status)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sync_pull(request):
    payload, status = services.svc_sync_pull(request.user.identity, {})
    return Response(payload, status=status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def sync_push(request):
    payload, status = services.svc_sync_push(request.user.identity, request.data)
    return Response(payload, status=status)


@api_view(["GET"])
@permission_classes([AllowAny])
def protocol_info(request):
    from . import protocol
    return Response(protocol.hello(), status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def motd(request):
    payload, status = services.svc_motd(request.user.identity, {})
    return Response(payload, status=status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notices(request):
    payload, status = services.svc_notices(request.user.identity, {})
    return Response(payload, status=status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quota(request):
    payload, status = services.svc_quota(request.user.identity, {})
    return Response(payload, status=status)
