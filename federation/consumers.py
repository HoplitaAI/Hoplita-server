import os
from concurrent.futures import ThreadPoolExecutor

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import close_old_connections
from rest_framework.authtoken.models import Token

from . import services
from .push import notify_user_async, notify_presence_async, _group


def _pool_size():
    try:
        return max(1, int(os.environ.get("HOPLITA_WS_POOL") or "8"))
    except ValueError:
        return 8


class FedConsumer(AsyncJsonWebsocketConsumer):

    _POOL = ThreadPoolExecutor(max_workers=_pool_size(),
                               thread_name_prefix="ws-op")

    @classmethod
    async def _db_as(cls, key, fn, params):
        def run():
            close_old_connections()
            try:
                identity = cls._identity_for(key)
                if identity is None:
                    return None, None, None
                payload, status = fn(identity, params)
                return identity, payload, status
            finally:
                close_old_connections()
        return await sync_to_async(run, thread_sensitive=False,
                                   executor=cls._POOL)()

    @classmethod
    async def _db(cls, fn, *args):
        def run():
            close_old_connections()
            try:
                return fn(*args)
            finally:
                close_old_connections()
        return await sync_to_async(run, thread_sensitive=False,
                                   executor=cls._POOL)()

    async def __call__(self, scope, receive, send):
        self.identity = None
        self._token_key = ""
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self.disconnect(None)
            except Exception:
                pass
            layer = getattr(self, "channel_layer", None)
            name = getattr(self, "channel_name", None)
            if layer is not None and name and hasattr(layer, "forget_channel"):
                layer.forget_channel(name)

    async def connect(self):
        self.identity = None
        self._token_key = ""
        await self.accept()

    async def disconnect(self, code):
        if self.identity is None:
            return
        identity, self.identity = self.identity, None
        try:
            await self.channel_layer.group_discard(
                _group(identity.id), self.channel_name)
            contact_ids = await self._db(self._set_presence, identity, False)
            await notify_presence_async(identity, False, contact_ids)
        except Exception:
            pass

    @staticmethod
    def _set_presence(identity, online):
        services.set_online(identity, online)
        return services.contact_ids_of(identity)

    async def receive_json(self, content, **kwargs):
        op = content.get("op")
        req_id = content.get("req_id")

        if op == "auth":
            await self._handle_auth(content)
            return

        if self.identity is None:
            await self.send_json({"req_id": req_id, "ok": False,
                                  "error": "Not authenticated: send "
                                           "{'op':'auth','token':...} first",
                                  "status": 401})
            return

        if op == "ping":
            try:
                identity, _p, _s = await self._db_as(
                    self._token_key,
                    lambda i, _: (services.touch_presence(i), 200), None)
            except Exception:
                identity = self.identity
            if identity is None:
                await self._revoked(req_id)
                return
            self.identity = identity
            await self.send_json({"req_id": req_id, "ok": True,
                                  "data": {"pong": True}})
            return

        fn = services.WS_OPS.get(op)
        if fn is None:
            await self.send_json({"req_id": req_id, "ok": False,
                                  "error": f"Unknown operation: {op}",
                                  "status": 400})
            return
        try:
            identity, payload, status = await self._db_as(
                self._token_key, self._throttled(fn),
                content.get("params") or {})
        except Exception as e:
            await self.send_json({"req_id": req_id, "ok": False,
                                  "error": f"Internal error: {e}",
                                  "status": 500})
            return
        if identity is None:
            await self._revoked(req_id)
            return
        self.identity = identity

        if status >= 400:
            err = dict(payload)
            msg = err.pop("error", "error")
            if "status" in err:
                err["resource_status"] = err.pop("status")
            await self.send_json({"req_id": req_id, "ok": False, "error": msg,
                                  "status": status, **err})
            return

        me = self.identity
        if op == "send_envelope":
            await notify_user_async(payload.pop("recipient_id"),
                                    "envelope.new", payload.pop("notice"))
        elif op in ("add_contact", "accept_contact", "remove_contact"):
            pid = payload.pop("peer_id", None)
            if pid:
                await notify_user_async(
                    pid, "contact.update",
                    {"from": me.handle,
                     "status": payload.get(
                         "status",
                         "removed" if op == "remove_contact" else "")})
        elif op == "share_folder":
            await notify_user_async(payload.pop("peer_id"), "folder.shared",
                                    {"from": me.handle,
                                     "folder_name": payload.get("folder_name"),
                                     "kind": payload.get("kind")})
        elif op == "accept_folder":
            await notify_user_async(payload.pop("peer_id"), "folder.accepted",
                                    {"by": me.handle,
                                     "folder_name": payload.get("folder_name"),
                                     "kind": payload.get("kind")})
        elif op == "unshare_folder":
            for pid in payload.pop("peer_ids", []):
                await notify_user_async(pid, "folder.unshared",
                                        {"by": me.handle,
                                         "folder_name": payload.get(
                                             "folder_name"),
                                         "kind": payload.get("kind")})
        else:
            payload.pop("peer_id", None)

        import json as _json
        try:
            size = len(_json.dumps(payload, default=str))
        except Exception:
            size = 0
        if size > 500_000:
            await self.send_json({"req_id": req_id, "ok": True,
                                  "data": {"use_rest": True, "size": size}})
            return

        await self.send_json({"req_id": req_id, "ok": True, "data": payload})

    @staticmethod
    def _identity_for(key):
        tok = Token.objects.filter(key=key).select_related("user").first()
        if tok is None or not hasattr(tok.user, "identity"):
            return None
        return tok.user.identity

    @staticmethod
    def _throttled(fn):
        def run(identity, params):
            from . import throttles
            allowed, retry = throttles.allow_identity(identity,
                                                      identity.user_id)
            if not allowed:
                return ({"error": "throttled", "retry_after": retry,
                         "message": ("Too many requests for your plan. This "
                                     "is flow control: wait and carry on.")},
                        429)
            return fn(identity, params)
        return run

    async def _revoked(self, req_id):
        await self.send_json({"req_id": req_id, "ok": False,
                              "error": "Invalid token", "status": 401})
        await self.close(code=4401)

    async def _handle_auth(self, content):
        req_id = content.get("req_id")
        key = (content.get("token") or "").strip()
        identity = await self._db(self._identity_for, key)
        if identity is None:
            await self.send_json({"req_id": req_id, "ok": False,
                                  "error": "Invalid token", "status": 401})
            await self.close(code=4401)
            return
        self.identity = identity
        self._token_key = key
        await self.channel_layer.group_add(
            _group(self.identity.id), self.channel_name)
        contact_ids = await self._db(self._set_presence, self.identity, True)
        await notify_presence_async(self.identity, True, contact_ids)
        await self.send_json({"req_id": req_id, "ok": True,
                              "data": {"handle": self.identity.handle,
                                       "authenticated": True}})

    async def fed_event(self, message):
        try:
            await self.send_json({"event": message["event"],
                                  "data": message["data"]})
        except Exception:
            pass
