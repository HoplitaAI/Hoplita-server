import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

log = logging.getLogger(__name__)


def _group(identity_id):
    return f"fed_user_{identity_id}"


async def _push_async(group, message):
    layer = get_channel_layer()
    if layer is None:
        return False
    try:
        await layer.group_send(group, message)
        return True
    except Exception as e:
        log.warning("push to %s dropped (%s: %s) — the write stands and "
                    "catch-up delivers it", group, type(e).__name__, e)
        return False


def _push(group, message):
    return async_to_sync(_push_async)(group, message)


async def notify_user_async(identity_id, event, data):
    return await _push_async(
        _group(identity_id), {"type": "fed.event", "event": event,
                              "data": data})


def notify_user(identity_id, event, data):
    return async_to_sync(notify_user_async)(identity_id, event, data)


async def reset_live_async(*identity_ids):
    for iid in identity_ids:
        if iid:
            await _push_async(_group(iid), {"type": "fed.live_reset"})


def reset_live(*identity_ids):
    return async_to_sync(reset_live_async)(*identity_ids)


def push_notice(recipient=None, kind="system", level="info", text="",
                code="", params=None, expires_at=None):
    from .models import ServerNotice
    n = ServerNotice.objects.create(
        recipient=recipient, kind=kind, level=level, text=text or "",
        code=code or "", params=params or {}, expires_at=expires_at)
    if recipient is not None:
        notify_user(recipient.id, "notice", n.as_payload())
    return n


async def notify_presence_async(identity, online, contact_ids):
    payload = {"type": "fed.event", "event": "presence",
               "data": {"handle": identity.handle, "online": bool(online)}}
    groups = [_group(cid) for cid in contact_ids]
    if not groups:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    if hasattr(layer, "group_send_many"):
        try:
            await layer.group_send_many(groups, payload)
            return
        except Exception as e:
            log.warning("presence fan-out dropped (%s: %s) — presence heals "
                        "on the next list_contacts", type(e).__name__, e)
            return
    for g in groups:
        await _push_async(g, payload)


def notify_presence(identity, online, contact_ids):
    async_to_sync(notify_presence_async)(identity, online, contact_ids)
