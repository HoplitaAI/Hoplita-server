from django.db.models import Sum

from .models import QuotaTier, Contact, Envelope, Identity, SyncSnapshot


def effective_quota(identity):
    tier = identity.tier or QuotaTier.get_default()
    base = tier.as_dict() if tier else {f: 0 for f in QuotaTier.FIELDS}
    overrides = identity.quota_overrides or {}
    for f in QuotaTier.FIELDS:
        if f in overrides:
            try:
                base[f] = int(overrides[f])
            except (TypeError, ValueError):
                pass
    return base


def current_usage(identity):
    snap = SyncSnapshot.objects.filter(owner=identity).first()
    inbox = Envelope.objects.filter(recipient=identity, delivered=False)
    outbox = Envelope.objects.filter(sender=identity, delivered=False)
    return {
        "sync_bytes": snap.size if snap else 0,
        "inbox_envelopes": inbox.count(),
        "inbox_bytes": inbox.aggregate(s=Sum("size_hint"))["s"] or 0,
        "outbox_envelopes": outbox.count(),
        "outbox_bytes": outbox.aggregate(s=Sum("size_hint"))["s"] or 0,
        "contacts": Contact.objects.filter(owner=identity).exclude(
            status=Contact.BLOCKED).count(),
        "children": Identity.objects.filter(spawned_from__owner=identity).count(),
    }


def _over(limit, used, incoming=0):
    return bool(limit) and (used + incoming) > limit


_SENDER_PAYS = {"outbox_envelopes": "max_inbox_envelopes",
                "outbox_bytes": "max_inbox_bytes"}


def check_quota(identity, resource, incoming=0):
    q = effective_quota(identity)
    u = current_usage(identity)
    checks = {
        "sync_bytes":      (q["max_sync_bytes"], 0, incoming),
        "inbox_envelopes": (q["max_inbox_envelopes"], u["inbox_envelopes"], 1),
        "inbox_bytes":     (q["max_inbox_bytes"], u["inbox_bytes"], incoming),
        "outbox_envelopes": (q["max_inbox_envelopes"], u["outbox_envelopes"], 1),
        "outbox_bytes":    (q["max_inbox_bytes"], u["outbox_bytes"], incoming),
        "contacts":        (q["max_contacts"], u["contacts"], 1),
        "children":        (q["max_children"], u["children"], 1),
    }
    if resource not in checks:
        return None
    limit, used, inc = checks[resource]
    if _over(limit, used, inc):
        return ({"error": "quota_exceeded", "resource": resource,
                 "limit": limit, "used": used, "incoming": inc,
                 "message": f"Quota exceeded ({resource}: {used}+{inc} > {limit}). "
                            f"Upgrade your plan or ask the operator for more."}, 402)
    return None
