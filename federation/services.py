import os
import re

from django.db.models import Q, Sum
from datetime import timedelta

from django.utils import timezone

from .models import (Identity, Contact, Envelope, SharedFolder,
                     SyncSnapshot, SyncUpload, SyncPart, ServerNotice,
                     PublicFolder, BannedEmail)
from . import quotas

MAX_ENVELOPE_BYTES = int(os.environ.get("HOPLITA_MAX_ENVELOPE_BYTES") or 4_000_000)
INBOX_PAGE_MAX = int(os.environ.get("HOPLITA_INBOX_PAGE_MAX") or 200)
INBOX_PAGE_BYTES = int(os.environ.get("HOPLITA_INBOX_PAGE_BYTES") or MAX_ENVELOPE_BYTES)
INBOX_LEGACY_PAGE_BYTES = int(os.environ.get("HOPLITA_INBOX_LEGACY_PAGE_BYTES")
                              or 64_000_000)
MAX_IDS_PER_CALL = int(os.environ.get("HOPLITA_MAX_IDS_PER_CALL") or 1000)
MAIL_TTL_DAYS = int(os.environ.get("HOPLITA_MAIL_TTL_DAYS") or 30)
_RTYPE_RE = re.compile(r"^[a-z0-9._-]{1,40}$")

MAX_ATTESTATION_BYTES = 4096


def _attestation_ok(text):
    return isinstance(text, str) and len(text) <= MAX_ATTESTATION_BYTES



def touch_presence(identity):
    from django.utils import timezone
    now = timezone.now()
    if identity.last_seen and (now - identity.last_seen).total_seconds() < 30:
        return
    identity.last_seen = now
    identity.save(update_fields=["last_seen"])


def clear_stale_presence():
    from .models import Identity
    return Identity.objects.filter(ws_online=True).update(ws_online=False,
                                                          ws_conns=0)


def set_online(identity, online):
    from django.db.models import F
    from .models import Identity
    step = 1 if online else -1
    Identity.objects.filter(pk=identity.pk).update(
        ws_conns=F("ws_conns") + step, last_seen=timezone.now())
    row = Identity.objects.filter(pk=identity.pk).only("ws_conns").first()
    live = max(0, int(getattr(row, "ws_conns", 0) or 0))
    Identity.objects.filter(pk=identity.pk).update(ws_conns=live,
                                                   ws_online=live > 0)
    identity.ws_conns = live
    identity.ws_online = live > 0
    identity.last_seen = timezone.now()


def contact_ids_of(identity):
    acc = Contact.ACCEPTED
    ids = set(Contact.objects.filter(owner=identity, status=acc)
              .values_list("peer_id", flat=True))
    ids |= set(Contact.objects.filter(peer=identity, status=acc)
               .values_list("owner_id", flat=True))
    ids.discard(identity.id)
    return ids



def svc_list_contacts(me, params=None):
    out = []
    for c in me.contacts.select_related("peer").all():
        out.append({
            "handle": c.peer.handle,
            "status": c.status,
            "trust_level": c.trust_level,
            "online": c.peer.is_online if c.status == Contact.ACCEPTED else False,
            "fingerprint": c.peer.fingerprint,
            "permissions": c.permissions,
            "attestation": c.attestation,
            "sign_pub": c.peer.sign_pub,
        })
    return {"contacts": out}, 200


def svc_add_contact(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    trust = params.get("trust_level", Contact.TRUST_ACCEPT)
    attestation = params.get("attestation") or ""
    if not _attestation_ok(attestation):
        return {"error": "attestation_too_big"}, 400
    if peer_handle == me.handle:
        return {"error": "You cannot add yourself"}, 400
    err = quotas.check_quota(me, "contacts", incoming=1)
    if err:
        return err
    try:
        peer = Identity.objects.get(handle=peer_handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404

    their_request = Contact.objects.filter(owner=peer, peer=me).first()
    mine = Contact.objects.filter(owner=me, peer=peer).first()

    if their_request and their_request.status in (Contact.PENDING,
                                                  Contact.PENDING_INCOMING):
        their_request.status = Contact.ACCEPTED
        their_request.save(update_fields=["status"])
        if mine:
            mine.status = Contact.ACCEPTED
            mine.attestation = attestation or mine.attestation
            mine.save(update_fields=["status", "attestation"])
        else:
            Contact.objects.create(owner=me, peer=peer, trust_level=trust,
                                   status=Contact.ACCEPTED,
                                   attestation=attestation)
        return {"ok": True, "status": Contact.ACCEPTED,
                "peer": peer_handle, "peer_id": peer.id,
                "note": "crossed requests → accepted"}, 201

    if mine:
        return {"error": "The relationship already exists", "status": mine.status}, 409

    Contact.objects.create(owner=me, peer=peer, trust_level=trust,
                           status=Contact.PENDING, attestation=attestation)
    Contact.objects.get_or_create(
        owner=peer, peer=me,
        defaults={"trust_level": Contact.TRUST_ACCEPT,
                  "status": Contact.PENDING_INCOMING})
    return {"ok": True, "status": Contact.PENDING, "peer": peer_handle,
            "peer_id": peer.id}, 201


def svc_accept_contact(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    trust = params.get("trust_level", Contact.TRUST_ACCEPT)
    attestation = params.get("attestation") or ""
    if not _attestation_ok(attestation):
        return {"error": "attestation_too_big"}, 400
    try:
        peer = Identity.objects.get(handle=peer_handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404

    mine = Contact.objects.filter(owner=me, peer=peer).first()
    if not mine or mine.status not in (Contact.PENDING_INCOMING,
                                       Contact.PENDING):
        return {"error": "No pending request from that handle"}, 404
    mine.status = Contact.ACCEPTED
    mine.trust_level = trust
    if attestation:
        mine.attestation = attestation
    mine.save(update_fields=["status", "trust_level", "attestation"])
    theirs = Contact.objects.filter(owner=peer, peer=me).first()
    if theirs:
        theirs.status = Contact.ACCEPTED
        theirs.save(update_fields=["status"])
    return {"ok": True, "peer": peer_handle, "peer_id": peer.id,
            "status": "accepted", "my_trust": trust}, 200


def svc_remove_contact(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    try:
        peer = Identity.objects.get(handle=peer_handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404

    removed = Contact.objects.filter(
        Q(owner=me, peer=peer) | Q(owner=peer, peer=me)).delete()
    Envelope.objects.filter(
        Q(sender=me, recipient=peer) | Q(sender=peer, recipient=me),
        delivered=False).delete()
    return {"ok": True, "peer": peer_handle, "peer_id": peer.id,
            "removed": removed[0] if removed else 0}, 200


def svc_set_permissions(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    resource_type = (params.get("resource_type") or "").strip()
    perms = params.get("perms")
    if not resource_type or not isinstance(perms, dict):
        return {"error": "Missing resource_type or perms (object)"}, 400
    try:
        contact = Contact.objects.get(owner=me, peer__handle=peer_handle)
    except Contact.DoesNotExist:
        return {"error": "Contact not found"}, 404

    current = contact.permissions or {}
    merged = dict(current.get(resource_type, {}))
    merged.update(perms)
    current[resource_type] = merged
    contact.permissions = current
    contact.save(update_fields=["permissions"])
    return {"ok": True, "handle": peer_handle,
            "resource_type": resource_type,
            "permissions": contact.permissions}, 200


def svc_get_key(me, params):
    handle = (params.get("handle") or "").strip().lower()
    try:
        identity = Identity.objects.get(handle=handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404
    return {"handle": identity.handle,
            "public_key": identity.public_key,
            "sign_pub": identity.sign_pub,
            "fingerprint": identity.fingerprint}, 200


def svc_publish_sign_key(me, params):
    sign_pub = (params.get("sign_pub") or "").strip()
    if not sign_pub or len(sign_pub) > 128:
        return {"error": "Missing sign_pub (or it is not shaped like a key)"}, 400
    if me.sign_pub and me.sign_pub != sign_pub:
        return {"error": "sign_key_exists",
                "message": "A different signing key is already published "
                           "for this identity."}, 409
    if not me.sign_pub:
        me.sign_pub = sign_pub
        me.save(update_fields=["sign_pub"])
    return {"ok": True, "sign_pub": me.sign_pub}, 200


def svc_attest_contact(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    attestation = params.get("attestation") or ""
    if not peer_handle or not attestation:
        return {"error": "Missing handle or attestation"}, 400
    if not _attestation_ok(attestation):
        return {"error": "attestation_too_big"}, 400
    contact = Contact.objects.filter(owner=me,
                                     peer__handle=peer_handle).first()
    if contact is None:
        return {"error": "Contact not found"}, 404
    contact.attestation = attestation
    contact.save(update_fields=["attestation"])
    return {"ok": True, "handle": peer_handle}, 200



def svc_send_envelope(me, params):
    recipient_handle = (params.get("recipient") or "").strip().lower()
    resource_type = params.get("resource_type")
    ciphertext = params.get("ciphertext")
    meta = params.get("meta", {})
    if not (recipient_handle and resource_type and ciphertext):
        return {"error": "Missing fields (recipient, resource_type, "
                         "ciphertext)"}, 400
    if len(ciphertext) > MAX_ENVELOPE_BYTES:
        return {"error": "envelope_too_big",
                "message": f"one envelope may not exceed "
                           f"{MAX_ENVELOPE_BYTES // 1024} KB "
                           f"({len(ciphertext) // 1024} KB sent). Split it: "
                           f"every transfer the clients do is already "
                           f"chunked."}, 413
    if not _RTYPE_RE.match(resource_type or ""):
        return {"error": "bad_resource_type",
                "message": "resource_type must be up to 40 chars of "
                           "[a-z0-9._-]: it is a routing label, not content."}, 400
    try:
        recipient = Identity.objects.get(handle=recipient_handle)
    except Identity.DoesNotExist:
        return {"error": "Recipient not found"}, 404

    if recipient.id != me.id and not Contact.objects.filter(
            owner=me, peer=recipient, status=Contact.ACCEPTED).exists():
        _serves = SharedFolder.objects.filter(
            owner=me, peer=recipient, readonly=True,
            status=SharedFolder.ACCEPTED).exists()
        if not _serves:
            return {"error": "Not an accepted contact"}, 403

    _fid = (meta or {}).get("folder_id") or (meta or {}).get("project_id")
    if _fid and resource_type in ("folder_note", "project_task", "project_board",
                                  "folder_del", "project_del"):
        _ro = SharedFolder.objects.filter(
            owner=recipient, peer=me, folder_id=_fid, readonly=True).exists()
        _rw = SharedFolder.objects.filter(
            Q(owner=recipient, peer=me) | Q(owner=me, peer=recipient),
            folder_id=_fid, readonly=False,
            status=SharedFolder.ACCEPTED).exists()
        if _ro and not _rw:
            return {"error": "read_only_member",
                    "message": "This folder is public and you subscribed "
                               "read-only: updates flow from the owner to "
                               "you, never back."}, 403

    for res, inc in (("outbox_envelopes", 1), ("outbox_bytes", len(ciphertext))):
        err = quotas.check_quota(me, res, incoming=inc)
        if err:
            return err

    env = Envelope.objects.create(
        sender=me, recipient=recipient, resource_type=resource_type,
        ciphertext=ciphertext, size_hint=len(ciphertext), meta=meta)
    return {"ok": True, "envelope_id": env.id,
            "recipient_id": recipient.id,
            "notice": envelope_to_notice(env)}, 201


def live_peer(me, handle):
    handle = (handle or "").strip().lower()
    if not handle:
        return None
    if handle == me.handle:
        return me.id
    peer = Identity.objects.filter(handle=handle).only("id").first()
    if peer is None:
        return None
    if not Contact.objects.filter(owner=me, peer=peer,
                                  status=Contact.ACCEPTED).exists():
        return None
    return peer.id


def envelope_to_dict(env):
    return {
        "id": env.id,
        "sender": env.sender.handle,
        "resource_type": env.resource_type,
        "ciphertext": env.ciphertext,
        "meta": env.meta,
        "created_at": env.created_at.isoformat(),
    }


def envelope_to_notice(env):
    return {
        "id": env.id,
        "sender": env.sender.handle,
        "resource_type": env.resource_type,
        "meta": env.meta,
        "created_at": env.created_at.isoformat(),
    }


def _id_list(raw):
    if isinstance(raw, str):
        raw = [x for x in raw.split(",") if x.strip()]
        try:
            raw = [int(x) for x in raw]
        except ValueError:
            return None
    if not isinstance(raw, list) or not all(isinstance(i, int) for i in raw):
        return None
    return raw


def _too_many_ids(ids):
    return ({"error": "too_many_ids",
             "message": f"at most {MAX_IDS_PER_CALL} ids per call; split it."},
            400)


def svc_fetch_inbox(me, params):
    params = params or {}
    rtype = params.get("type")
    rtypes = params.get("types")
    peek = str(params.get("peek") or "") in ("1", "true", "yes", "True")
    bodies = str(params.get("bodies", "1")).strip().lower() not in (
        "0", "false", "no")
    paged = str(params.get("paged") or "") in ("1", "true", "yes", "True")
    page_bytes = INBOX_PAGE_BYTES if paged else INBOX_LEGACY_PAGE_BYTES
    try:
        after = int(params.get("after") or 0)
    except (TypeError, ValueError):
        after = 0
    ids = None
    if params.get("ids") not in (None, "", []):
        ids = _id_list(params.get("ids"))
        if ids is None:
            return {"error": "ids must be a list of integers"}, 400
        if len(ids) > MAX_IDS_PER_CALL:
            return _too_many_ids(ids)
    qs = Envelope.objects.filter(recipient=me, delivered=False)
    if rtypes:
        if isinstance(rtypes, str):
            rtypes = [t.strip() for t in rtypes.split(",") if t.strip()]
        if rtypes:
            qs = qs.filter(resource_type__in=rtypes)
    elif rtype:
        qs = qs.filter(resource_type=rtype)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    if after > 0:
        qs = qs.filter(pk__gt=after)
    qs = qs.select_related("sender").order_by("pk")
    if not bodies:
        qs = qs.defer("ciphertext")
    out = []
    consumed = []
    body = 0
    more = False
    for env in qs.iterator(chunk_size=64):
        size = env.size_hint or (len(env.ciphertext or "") if bodies else 0)
        if out and (len(out) >= INBOX_PAGE_MAX
                    or (bodies and body + size > page_bytes)):
            more = True
            break
        if bodies:
            body += size
            out.append(envelope_to_dict(env))
            if not peek:
                consumed.append(env.pk)
        else:
            head = envelope_to_notice(env)
            head["size"] = size
            out.append(head)
    if consumed:
        Envelope.objects.filter(pk__in=consumed).delete()
    res = {"envelopes": out, "count": len(out)}
    if not bodies:
        res["bodies"] = False
    if more:
        res["more"] = True
        res["next_after"] = out[-1]["id"]
    return res, 200


def sweep_stale_mail(days=None):
    from .models import EmailPin, LoginChallenge
    legacy, _ = Envelope.objects.filter(delivered=True).delete()
    now = timezone.now()
    LoginChallenge.objects.filter(
        Q(used=True) | Q(expires_at__lt=now)).delete()
    EmailPin.objects.filter(
        Q(used=True) | Q(created_at__lt=now - timedelta(
            minutes=EmailPin.PIN_TTL_MINUTES))).delete()
    days = MAIL_TTL_DAYS if days is None else int(days)
    if days <= 0:
        return {"swept": 0, "freed_bytes": 0, "ttl_days": days,
                "legacy_delivered": legacy}
    cutoff = now - timedelta(days=days)
    qs = Envelope.objects.filter(delivered=False, created_at__lt=cutoff)
    freed = qs.aggregate(s=Sum("size_hint"))["s"] or 0
    n, _ = qs.delete()
    return {"swept": n, "freed_bytes": freed, "ttl_days": days,
            "legacy_delivered": legacy}


def svc_withdraw_envelopes(me, params):
    ids = params.get("ids") or []
    rtype = (params.get("resource_type") or "").strip()
    to = (params.get("recipient") or "").strip()
    qs = Envelope.objects.filter(sender=me, delivered=False)
    if ids:
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            return {"error": "ids must be a list of integers"}, 400
        if len(ids) > MAX_IDS_PER_CALL:
            return _too_many_ids(ids)
        qs = qs.filter(id__in=ids)
    if rtype:
        qs = qs.filter(resource_type=rtype)
    if to:
        peer = Identity.objects.filter(handle=to).first()
        if not peer:
            return {"error": "Recipient not found"}, 404
        qs = qs.filter(recipient=peer)
    if not (ids or rtype or to):
        return {"error": "say WHAT to withdraw (ids, resource_type or "
                         "recipient): withdrawing everything by accident is "
                         "not a feature"}, 400
    freed = qs.aggregate(s=Sum("size_hint"))["s"] or 0
    n, _ = qs.delete()
    return {"ok": True, "withdrawn": n, "freed_bytes": freed}, 200


def svc_ack_envelopes(me, params):
    ids = params.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return {"error": "ids must be a list of integers"}, 400
    if len(ids) > MAX_IDS_PER_CALL:
        return _too_many_ids(ids)
    n, _ = Envelope.objects.filter(recipient=me, id__in=ids).delete()
    return {"ok": True, "acked": n}, 200



def svc_share_folder(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    folder_id = (params.get("folder_id") or "").strip()
    folder_name = (params.get("folder_name") or "").strip()
    kind = (params.get("kind") or SharedFolder.KIND_FOLDER).strip()
    grant = params.get("grant") or ""
    if not _attestation_ok(grant):
        return {"error": "attestation_too_big"}, 400
    if kind not in (SharedFolder.KIND_FOLDER, SharedFolder.KIND_PROJECT):
        kind = SharedFolder.KIND_FOLDER
    if not folder_id or not folder_name:
        return {"error": "Missing folder_id or folder_name"}, 400
    if peer_handle == me.handle:
        return {"error": "You cannot share with yourself"}, 400
    try:
        peer = Identity.objects.get(handle=peer_handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404
    if not Contact.objects.filter(owner=me, peer=peer,
                                  status=Contact.ACCEPTED).exists():
        return {"error": "You must be an (accepted) contact to share "
                         "folders"}, 403
    sf, created = SharedFolder.objects.get_or_create(
        owner=me, peer=peer, folder_id=folder_id,
        defaults={"folder_name": folder_name, "kind": kind, "grant": grant})
    if not created:
        noun = ("project" if sf.kind == SharedFolder.KIND_PROJECT
                else "folder")
        return {"error": f"That {noun} is already shared",
                "status": sf.status, "kind": sf.kind}, 409
    return {"ok": True, "folder_id": folder_id, "folder_name": folder_name,
            "peer": peer_handle, "peer_id": peer.id,
            "status": sf.status, "kind": sf.kind}, 201


def svc_accept_folder(me, params):
    peer_handle = (params.get("handle") or "").strip().lower()
    folder_id = (params.get("folder_id") or "").strip()
    try:
        peer = Identity.objects.get(handle=peer_handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404
    incoming = SharedFolder.objects.filter(owner=peer, peer=me,
                                           folder_id=folder_id).first()
    if not incoming:
        return {"error": "No proposal for that folder"}, 404
    incoming.status = SharedFolder.ACCEPTED
    incoming.save(update_fields=["status"])
    return {"ok": True, "folder_id": folder_id,
            "folder_name": incoming.folder_name,
            "peer": peer_handle, "peer_id": peer.id,
            "kind": incoming.kind}, 200


def svc_grant_folder(me, params):
    folder_id = (params.get("folder_id") or "").strip()
    peer_handle = (params.get("peer_handle") or "").strip().lower()
    grant = params.get("grant") or ""
    if not folder_id or not peer_handle or not grant:
        return {"error": "Missing folder_id, peer_handle or grant"}, 400
    if not _attestation_ok(grant):
        return {"error": "attestation_too_big"}, 400
    sf = SharedFolder.objects.filter(owner=me, folder_id=folder_id,
                                     peer__handle=peer_handle).first()
    if sf is None:
        return {"error": "No such link of yours with that peer"}, 404
    sf.grant = grant
    sf.save(update_fields=["grant"])
    return {"ok": True, "folder_id": folder_id, "peer": peer_handle}, 200


def svc_unshare_folder(me, params):
    folder_id = (params.get("folder_id") or "").strip()
    peer_handle = (params.get("peer_handle") or "").strip().lower()
    if not folder_id:
        return {"error": "Missing folder_id"}, 400
    mine = SharedFolder.objects.filter(folder_id=folder_id).filter(
        Q(owner=me) | Q(peer=me))
    if peer_handle:
        mine = mine.filter(Q(owner=me, peer__handle=peer_handle)
                           | Q(peer=me, owner__handle=peer_handle))
    if not mine.exists():
        return {"error": "You take no part in that folder"}, 404
    others = set()
    name = ""
    kind = SharedFolder.KIND_FOLDER
    for sf in mine:
        name = sf.folder_name
        kind = sf.kind
        others.add(sf.peer_id if sf.owner_id == me.id else sf.owner_id)
    removed, _ = mine.delete()
    others.discard(me.id)
    return {"ok": True, "folder_id": folder_id, "folder_name": name,
            "kind": kind, "removed": removed,
            "peer_ids": sorted(others)}, 200


def svc_list_folders(me, params=None):
    out = []
    for sf in SharedFolder.objects.filter(owner=me).select_related("peer"):
        out.append({"folder_id": sf.folder_id, "folder_name": sf.folder_name,
                    "with": sf.peer.handle, "status": sf.status,
                    "role": "owner", "kind": sf.kind,
                    "readonly": sf.readonly, "grant": sf.grant})
    for sf in SharedFolder.objects.filter(peer=me).select_related("owner"):
        if sf.owner_id == me.id:
            continue
        out.append({"folder_id": sf.folder_id, "folder_name": sf.folder_name,
                    "with": sf.owner.handle, "status": sf.status,
                    "role": "peer", "kind": sf.kind,
                    "readonly": sf.readonly, "grant": sf.grant})
    pub = [{"folder_id": p.folder_id, "folder_name": p.folder_name,
            "kind": p.kind, "subscribers": p.subscriber_count(),
            "attestation": p.attestation}
           for p in PublicFolder.objects.filter(owner=me)]
    return {"folders": out, "public": pub}, 200



def svc_publish_folder(me, params):
    folder_id = (params.get("folder_id") or "").strip()
    folder_name = (params.get("folder_name") or "").strip()
    kind = (params.get("kind") or SharedFolder.KIND_FOLDER).strip()
    attestation = params.get("attestation") or ""
    if not _attestation_ok(attestation):
        return {"error": "attestation_too_big"}, 400
    if kind not in (SharedFolder.KIND_FOLDER, SharedFolder.KIND_PROJECT):
        kind = SharedFolder.KIND_FOLDER
    if not folder_id or not folder_name:
        return {"error": "Missing folder_id or folder_name"}, 400
    pf, created = PublicFolder.objects.get_or_create(
        owner=me, folder_id=folder_id,
        defaults={"folder_name": folder_name, "kind": kind,
                  "attestation": attestation})
    if not created and (pf.folder_name != folder_name or pf.kind != kind
                        or (attestation and pf.attestation != attestation)):
        pf.folder_name, pf.kind = folder_name, kind
        if attestation:
            pf.attestation = attestation
        pf.save(update_fields=["folder_name", "kind", "attestation"])
    return {"ok": True, "published": True, "created": created,
            "folder_id": folder_id, "folder_name": folder_name,
            "kind": kind, "subscribers": pf.subscriber_count()}, (
                201 if created else 200)


def svc_unpublish_folder(me, params):
    folder_id = (params.get("folder_id") or "").strip()
    if not folder_id:
        return {"error": "Missing folder_id"}, 400
    n = PublicFolder.objects.filter(owner=me, folder_id=folder_id).delete()[0]
    if not n:
        return {"error": "Not a public folder of yours"}, 404
    dropped = SharedFolder.objects.filter(owner=me, folder_id=folder_id,
                                          readonly=True).delete()[0]
    return {"ok": True, "unpublished": folder_id,
            "subscribers_dropped": dropped}, 200


def svc_public_folders(me, params):
    handle = (params.get("handle") or "").strip().lower()
    if not handle:
        return {"error": "Missing handle"}, 400
    try:
        who = Identity.objects.get(handle=handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404
    mine = set(SharedFolder.objects.filter(
        owner=who, peer=me, readonly=True).values_list("folder_id", flat=True))
    out = [{"folder_id": p.folder_id, "folder_name": p.folder_name,
            "kind": p.kind, "subscribers": p.subscriber_count(),
            "subscribed": p.folder_id in mine}
           for p in PublicFolder.objects.filter(owner=who)]
    return {"handle": handle, "public_folders": out}, 200


def svc_subscribe_folder(me, params):
    handle = (params.get("handle") or "").strip().lower()
    folder_id = (params.get("folder_id") or "").strip()
    if not handle or not folder_id:
        return {"error": "Missing handle or folder_id"}, 400
    try:
        owner = Identity.objects.get(handle=handle)
    except Identity.DoesNotExist:
        return {"error": "Handle not found"}, 404
    if owner.id == me.id:
        return {"error": "It is yours: you do not subscribe to your own folder"}, 400
    try:
        pf = PublicFolder.objects.get(owner=owner, folder_id=folder_id)
    except PublicFolder.DoesNotExist:
        return {"error": "That folder is not public"}, 404
    if BannedEmail.is_banned(getattr(me.user, "email", "")):
        return {"error": "banned",
                "message": "This account's email is banned on this "
                           "server."}, 403
    sf, created = SharedFolder.objects.get_or_create(
        owner=owner, peer=me, folder_id=folder_id,
        defaults={"folder_name": pf.folder_name, "kind": pf.kind,
                  "status": SharedFolder.ACCEPTED, "readonly": True})
    if not created and not sf.readonly:
        return {"error": "You already share this folder in full mode"}, 409
    return {"ok": True, "subscribed": True, "created": created,
            "folder_id": folder_id, "folder_name": pf.folder_name,
            "kind": pf.kind, "owner": owner.handle,
            "owner_id": owner.id,
            "subscribers": pf.subscriber_count()}, (201 if created else 200)


def svc_unsubscribe_folder(me, params):
    handle = (params.get("handle") or "").strip().lower()
    folder_id = (params.get("folder_id") or "").strip()
    if not handle or not folder_id:
        return {"error": "Missing handle or folder_id"}, 400
    n = SharedFolder.objects.filter(
        owner__handle=handle, peer=me, folder_id=folder_id,
        readonly=True).delete()[0]
    if not n:
        return {"error": "You were not subscribed"}, 404
    return {"ok": True, "unsubscribed": folder_id}, 200


def svc_sync_pull(me, params=None):
    snap = SyncSnapshot.objects.filter(owner=me).first()
    if snap is None:
        return {"version": 0, "ciphertext": None}, 200
    head = {"version": snap.version, "size": snap.size,
            "updated_at": snap.updated_at.isoformat()}
    if not snap.parts:
        return dict(head, ciphertext=snap.ciphertext), 200
    if _truthy((params or {}).get("parts")):
        return dict(head, ciphertext=None, parts=snap.parts,
                    sha256=snap.sha256), 200
    if snap.size > _max_body():
        return {"error": "snapshot_needs_parts",
                "message": "This snapshot is too big to come down in one "
                           "piece and this client cannot fetch it in parts: "
                           "update HOPLITA to sync it."}, 409
    blob = "".join(SyncPart.objects.filter(upload_id=snap.upload_id)
                   .order_by("idx").values_list("data", flat=True))
    return dict(head, ciphertext=blob), 200


def svc_sync_pull_part(me, params):
    params = params or {}
    snap = SyncSnapshot.objects.filter(owner=me).first()
    try:
        idx = int(params.get("idx"))
        version = int(params.get("version"))
    except (TypeError, ValueError):
        return {"error": "idx and version required"}, 400
    if snap is None or not snap.parts:
        return {"error": "no snapshot in parts"}, 404
    if version != snap.version:
        return {"error": "version_conflict", "server_version": snap.version}, 409
    part = SyncPart.objects.filter(upload_id=snap.upload_id, idx=idx).first()
    if part is None:
        return {"error": f"no part {idx}"}, 404
    return {"version": snap.version, "idx": idx, "data": part.data}, 200


def svc_sync_push(me, params):
    ciphertext = (params or {}).get("ciphertext")
    if not ciphertext:
        return {"error": "ciphertext required"}, 400
    err = quotas.check_quota(me, "sync_bytes", incoming=len(ciphertext))
    if err:
        return err
    base_version = int((params or {}).get("base_version", 0) or 0)
    snap = SyncSnapshot.objects.filter(owner=me).first()
    cur = snap.version if snap else 0
    if snap is not None and base_version != cur:
        return {"error": "version_conflict", "server_version": cur}, 409
    new_version = cur + 1
    if snap is None:
        snap = SyncSnapshot(owner=me)
    old_upload = snap.upload_id
    snap.version = new_version
    snap.ciphertext = ciphertext
    snap.size = len(ciphertext)
    snap.parts, snap.upload_id, snap.sha256 = 0, "", ""
    snap.save()
    if old_upload:
        SyncPart.objects.filter(upload_id=old_upload).delete()
    return {"ok": True, "version": new_version}, 200


def _max_body():
    from django.conf import settings
    return int(getattr(settings, "DATA_UPLOAD_MAX_MEMORY_SIZE", 0) or 2_621_440)


def sync_part_bytes():
    want = int(os.environ.get("HOPLITA_SYNC_PART_BYTES") or 8 * 1024 * 1024)
    return max(64 * 1024, min(want, _max_body() * 3 // 4))


SYNC_UPLOAD_TTL = timedelta(hours=6)
SYNC_UPLOADS_IN_FLIGHT = 3


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _drop_upload(up):
    SyncPart.objects.filter(upload_id=up.upload_id).delete()
    up.delete()


def svc_sync_push_begin(me, params):
    import secrets
    params = params or {}
    try:
        parts = int(params.get("parts"))
        size = int(params.get("size"))
        base_version = int(params.get("base_version", 0) or 0)
    except (TypeError, ValueError):
        return {"error": "parts, size and base_version must be numbers"}, 400
    sha = str(params.get("sha256") or "").lower()
    if parts < 1 or size < 1 or len(sha) != 64:
        return {"error": "parts, size and sha256 required"}, 400
    if parts > size or size > parts * sync_part_bytes():
        return {"error": f"{parts} parts cannot hold {size} bytes at "
                         f"{sync_part_bytes()} per part"}, 400
    err = quotas.check_quota(me, "sync_bytes", incoming=size)
    if err:
        return err
    snap = SyncSnapshot.objects.filter(owner=me).first()
    if snap is not None and base_version != snap.version:
        return {"error": "version_conflict", "server_version": snap.version}, 409
    for up in SyncUpload.objects.filter(
            owner=me, created_at__lt=timezone.now() - SYNC_UPLOAD_TTL):
        _drop_upload(up)
    if SyncUpload.objects.filter(owner=me).count() >= SYNC_UPLOADS_IN_FLIGHT:
        return {"error": "too_many_uploads",
                "message": "Several uploads of your snapshot are already on "
                           "their way; try again when they finish."}, 429
    up = SyncUpload.objects.create(owner=me, upload_id=secrets.token_hex(16),
                                   base_version=base_version, parts=parts,
                                   size=size, sha256=sha)
    return {"ok": True, "upload_id": up.upload_id,
            "part_bytes": sync_part_bytes()}, 200


def svc_sync_push_part(me, params):
    params = params or {}
    up = SyncUpload.objects.filter(owner=me,
                                   upload_id=str(params.get("upload_id") or "")).first()
    if up is None:
        return {"error": "upload_unknown",
                "message": "That upload is not open (finished, or swept)."}, 404
    try:
        idx = int(params.get("idx"))
    except (TypeError, ValueError):
        return {"error": "idx required"}, 400
    data = params.get("data")
    if not isinstance(data, str) or not data:
        return {"error": "data required"}, 400
    if not 0 <= idx < up.parts:
        return {"error": f"part {idx} outside 0..{up.parts - 1}"}, 400
    if len(data) > sync_part_bytes():
        return {"error": f"part over {sync_part_bytes()} bytes"}, 413
    others = (SyncPart.objects.filter(upload_id=up.upload_id)
              .exclude(idx=idx).aggregate(n=Sum("size"))["n"] or 0)
    if others + len(data) > up.size:
        return {"error": "the parts are bigger than the size declared"}, 400
    SyncPart.objects.update_or_create(
        upload_id=up.upload_id, idx=idx,
        defaults={"owner": me, "data": data, "size": len(data)})
    return {"ok": True, "idx": idx}, 200


def svc_sync_push_commit(me, params):
    import hashlib
    from django.db import IntegrityError, transaction
    params = params or {}
    up = SyncUpload.objects.filter(owner=me,
                                   upload_id=str(params.get("upload_id") or "")).first()
    if up is None:
        return {"error": "upload_unknown",
                "message": "That upload is not open (finished, or swept)."}, 404
    qs = SyncPart.objects.filter(upload_id=up.upload_id)
    have = qs.count()
    total = qs.aggregate(n=Sum("size"))["n"] or 0
    if have != up.parts or total != up.size:
        return {"error": "upload_incomplete", "parts": have, "of": up.parts,
                "size": total, "declared": up.size}, 400
    h = hashlib.sha256()
    for data in qs.order_by("idx").values_list("data", flat=True).iterator(chunk_size=1):
        h.update(data.encode("utf-8"))
    if h.hexdigest() != up.sha256:
        _drop_upload(up)
        return {"error": "hash_mismatch",
                "message": "The parts that arrived are not the snapshot that "
                           "was declared; nothing was changed."}, 400
    err = quotas.check_quota(me, "sync_bytes", incoming=up.size)
    if err:
        _drop_upload(up)
        return err
    snap = SyncSnapshot.objects.filter(owner=me).first()
    if snap is None:
        try:
            with transaction.atomic():
                SyncSnapshot.objects.create(
                    owner=me, version=1, ciphertext="", size=up.size,
                    parts=up.parts, upload_id=up.upload_id, sha256=up.sha256)
        except IntegrityError:
            _drop_upload(up)
            cur = SyncSnapshot.objects.filter(owner=me).values_list("version", flat=True).first()
            return {"error": "version_conflict", "server_version": cur}, 409
        up.delete()
        return {"ok": True, "version": 1}, 200
    if up.base_version != snap.version:
        _drop_upload(up)
        return {"error": "version_conflict", "server_version": snap.version}, 409
    won = SyncSnapshot.objects.filter(owner=me, version=up.base_version).update(
        version=up.base_version + 1, ciphertext="", size=up.size, parts=up.parts,
        upload_id=up.upload_id, sha256=up.sha256, updated_at=timezone.now())
    if not won:
        _drop_upload(up)
        cur = SyncSnapshot.objects.filter(owner=me).values_list("version", flat=True).first()
        return {"error": "version_conflict", "server_version": cur}, 409
    if snap.upload_id:
        SyncPart.objects.filter(upload_id=snap.upload_id).delete()
    up.delete()
    return {"ok": True, "version": up.base_version + 1}, 200


def svc_quota(me, params=None):
    tier = me.tier or None
    from .models import QuotaTier
    if tier is None:
        tier = QuotaTier.get_default()
    return {"tier": tier.name if tier else "none",
            "limits": quotas.effective_quota(me),
            "usage": quotas.current_usage(me)}, 200


def svc_motd(me=None, params=None):
    from django.utils import timezone
    now = timezone.now()
    m = (ServerNotice.objects
         .filter(active=True, kind=ServerNotice.MOTD, recipient__isnull=True)
         .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
         .order_by("-created_at").first())
    if m is None:
        return {"motd": None}, 200
    return {"motd": {"id": m.id, "text": m.text, "level": m.level,
                     "created_at": m.created_at.isoformat()}}, 200


def svc_notices(me=None, params=None):
    from django.utils import timezone
    now = timezone.now()
    live = ServerNotice.objects.filter(active=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    broadcast = live.filter(recipient__isnull=True)
    targeted = live.filter(recipient=me, delivered=False) if me else live.none()
    out = ([n.as_payload() for n in broadcast.order_by("created_at")] +
           [n.as_payload() for n in targeted.order_by("created_at")])
    if me:
        ServerNotice.objects.filter(active=True, recipient=me,
                                    delivered=False).update(
            delivered=True, delivered_at=now)
    return {"notices": out}, 200


WS_OPS = {
    "sync_pull": svc_sync_pull,
    "sync_push": svc_sync_push,
    "sync_pull_part": svc_sync_pull_part,
    "sync_push_begin": svc_sync_push_begin,
    "sync_push_part": svc_sync_push_part,
    "sync_push_commit": svc_sync_push_commit,
    "motd": svc_motd,
    "notices": svc_notices,
    "quota": svc_quota,
    "list_contacts": svc_list_contacts,
    "add_contact": svc_add_contact,
    "accept_contact": svc_accept_contact,
    "remove_contact": svc_remove_contact,
    "set_permissions": svc_set_permissions,
    "get_key": svc_get_key,
    "publish_sign_key": svc_publish_sign_key,
    "attest_contact": svc_attest_contact,
    "grant_folder": svc_grant_folder,
    "send_envelope": svc_send_envelope,
    "fetch_inbox": svc_fetch_inbox,
    "ack_envelopes": svc_ack_envelopes,
    "share_folder": svc_share_folder,
    "accept_folder": svc_accept_folder,
    "unshare_folder": svc_unshare_folder,
    "list_folders": svc_list_folders,
    "publish_folder": svc_publish_folder,
    "unpublish_folder": svc_unpublish_folder,
    "public_folders": svc_public_folders,
    "subscribe_folder": svc_subscribe_folder,
    "unsubscribe_folder": svc_unsubscribe_folder,
}
