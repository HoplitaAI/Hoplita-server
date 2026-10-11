import asyncio
import json
import logging
import random
import string
import time

import psycopg
from channels.exceptions import MessageTooLarge
from channels.layers import BaseChannelLayer

logger = logging.getLogger(__name__)

BUS = "hoplita_bus"
NOTIFY_MAX = 7500
ORPHAN_AFTER_S = 600
SWEEP_EVERY_S = 60


def _dsn_from_settings():
    from django.conf import settings
    db = settings.DATABASES["default"]
    parts = {"dbname": db.get("NAME"), "user": db.get("USER"),
             "password": db.get("PASSWORD"), "host": db.get("HOST"),
             "port": db.get("PORT")}
    return " ".join(f"{k}={v}" for k, v in parts.items() if v)


class _LoopState:

    def __init__(self):
        self.listen_task = None
        self.listen_conn = None
        self.send_conn = None
        self.send_lock = asyncio.Lock()
        self.queues = {}
        self.groups = {}
        self.waiting = {}
        self.member_of = {}
        self.last_seen = {}
        self.last_sweep = time.monotonic()


class PgChannelLayer(BaseChannelLayer):

    extensions = ["groups", "flush"]

    def __init__(self, dsn=None, expiry=30, capacity=1500,
                 group_expiry=86400, **kwargs):
        super().__init__(expiry=expiry, capacity=capacity, **kwargs)
        self._dsn = dsn
        self.group_expiry = group_expiry
        self._prefix = "".join(random.choices(string.ascii_lowercase, k=8))
        self._states = {}

    def _state(self):
        loop = asyncio.get_running_loop()
        st = self._states.get(loop)
        if st is None:
            st = self._states[loop] = _LoopState()
            self._wrap_close(loop)
        return st

    def _wrap_close(self, loop):
        states, orig = self._states, loop.close

        def close(*a, **kw):
            st = states.pop(loop, None)
            if st is not None:
                if st.listen_task is not None:
                    st.listen_task.cancel()
                for c in (st.listen_conn, st.send_conn):
                    if c is not None:
                        try:
                            loop.run_until_complete(c.close())
                        except Exception:
                            pass
            return orig(*a, **kw)

        loop.close = close

    def dsn(self):
        if self._dsn is None:
            self._dsn = _dsn_from_settings()
        return self._dsn

    @staticmethod
    def _encode(kind, message, **fields):
        payload = {"k": kind, "m": message, "t": time.time(), **fields}
        raw = json.dumps(payload, separators=(",", ":"), default=str)
        if len(raw.encode()) > NOTIFY_MAX:
            raise MessageTooLarge(
                f"{len(raw)} bytes on the bus. The bus carries NOTICES; "
                f"bodies travel in the database and are fetched. If you are "
                f"seeing this, something upstream is pushing content "
                f"through the bus; fix that, do not widen the pipe for it.")
        return raw

    async def _notify(self, raw):
        st = self._state()
        async with st.send_lock:
            for attempt in (1, 2):
                try:
                    if st.send_conn is None:
                        st.send_conn = await psycopg.AsyncConnection.connect(
                            self.dsn(), autocommit=True)
                    await st.send_conn.execute(
                        "SELECT pg_notify(%s, %s)", (BUS, raw))
                    return
                except Exception:
                    try:
                        if st.send_conn is not None:
                            await st.send_conn.close()
                    except Exception:
                        pass
                    st.send_conn = None
                    if attempt == 2:
                        raise

    async def send(self, channel, message):
        assert isinstance(message, dict), "message must be a dict"
        self._ensure_listener()
        await self._notify(self._encode("s", message, c=channel))

    async def group_send(self, group, message):
        assert isinstance(message, dict), "message must be a dict"
        await self._notify(self._encode("g", message, g=group))

    async def group_send_many(self, groups, message):
        assert isinstance(message, dict), "message must be a dict"
        groups = [g for g in groups if g]
        if not groups:
            return
        base = len(self._encode("G", message, gs=[]).encode())
        room = max(NOTIFY_MAX - base - 64, 256)
        batch, size = [], 0
        for g in groups:
            cost = len(g) + 3
            if batch and size + cost > room:
                await self._notify(self._encode("G", message, gs=batch))
                batch, size = [], 0
            batch.append(g)
            size += cost
        if batch:
            await self._notify(self._encode("G", message, gs=batch))

    def _ensure_listener(self):
        st = self._state()
        if st.listen_task is None or st.listen_task.done():
            st.listen_task = asyncio.get_running_loop().create_task(
                self._listen_forever(st), name="pg-bus-listener")
        return st

    async def _listen_forever(self, st):
        backoff = 0.5
        while True:
            try:
                conn = await psycopg.AsyncConnection.connect(
                    self.dsn(), autocommit=True)
                st.listen_conn = conn
                await conn.execute(f"LISTEN {BUS}")
                if backoff > 0.5:
                    logger.warning("bus listener reconnected — pushes during "
                                   "the gap are covered by client catch-up")
                backoff = 0.5
                async for n in conn.notifies():
                    self._dispatch(st, n.payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                st.listen_conn = None
                logger.warning(
                    "bus listener down (%s: %s) — retrying in %.1fs; the "
                    "writes are safe, only instant delivery pauses",
                    type(e).__name__, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def _dispatch(self, st, raw):
        try:
            p = json.loads(raw)
            kind, message = p.get("k"), p.get("m")
            deadline = float(p.get("t") or 0) + self.expiry
            if kind == "s":
                self._deliver(st, p.get("c"), message, deadline)
            elif kind == "g":
                self._deliver_group(st, p.get("g"), message, deadline)
            elif kind == "G":
                for g in p.get("gs") or []:
                    self._deliver_group(st, g, message, deadline)
        except Exception as e:
            logger.warning("bus payload dropped (%s: %s)", type(e).__name__, e)

    def _deliver_group(self, st, group, message, deadline):
        members = st.groups.get(group)
        if not members:
            return
        cutoff = time.monotonic() - self.group_expiry
        for channel, added in list(members.items()):
            if added < cutoff:
                members.pop(channel, None)
                mine = st.member_of.get(channel)
                if mine is not None:
                    mine.discard(group)
                    if not mine:
                        st.member_of.pop(channel, None)
                continue
            self._deliver(st, channel, message, deadline)
        if not members:
            st.groups.pop(group, None)

    def _deliver(self, st, channel, message, deadline):
        q = st.queues.get(channel)
        if q is None:
            return
        try:
            q.put_nowait((deadline, message))
        except asyncio.QueueFull:
            logger.warning("channel %s full: message dropped", channel)

    async def receive(self, channel):
        st = self._ensure_listener()
        q = st.queues.get(channel)
        if q is None:
            q = st.queues[channel] = asyncio.Queue(maxsize=self.capacity)
        st.waiting[channel] = st.waiting.get(channel, 0) + 1
        st.last_seen[channel] = time.monotonic()
        try:
            while True:
                deadline, message = await q.get()
                if time.time() > deadline:
                    continue
                return message
        except asyncio.CancelledError:
            if st.waiting.get(channel, 0) <= 1:
                self._forget(st, channel)
            raise
        finally:
            n = st.waiting.get(channel, 0) - 1
            if n > 0:
                st.waiting[channel] = n
            else:
                st.waiting.pop(channel, None)
            if channel in st.queues:
                st.last_seen[channel] = time.monotonic()
            self._sweep(st)

    def forget_channel(self, channel):
        try:
            self._forget(self._state(), channel)
        except RuntimeError:
            pass

    def _forget(self, st, channel):
        st.queues.pop(channel, None)
        st.last_seen.pop(channel, None)
        for group in st.member_of.pop(channel, ()):
            members = st.groups.get(group)
            if members:
                members.pop(channel, None)
                if not members:
                    st.groups.pop(group, None)

    def _sweep(self, st, now=None):
        now = time.monotonic() if now is None else now
        if now - st.last_sweep < SWEEP_EVERY_S:
            return 0
        st.last_sweep = now
        gone = [c for c in list(st.queues)
                if not st.waiting.get(c)
                and now - st.last_seen.get(c, now) > ORPHAN_AFTER_S]
        for c in gone:
            self._forget(st, c)
        return len(gone)

    async def new_channel(self, prefix="specific"):
        st = self._ensure_listener()
        name = (f"{prefix}.{self._prefix}!"
                f"{''.join(random.choices(string.ascii_letters, k=12))}")
        st.queues[name] = asyncio.Queue(maxsize=self.capacity)
        st.last_seen[name] = time.monotonic()
        self._sweep(st)
        return name

    async def group_add(self, group, channel):
        st = self._ensure_listener()
        if channel not in st.queues:
            st.queues[channel] = asyncio.Queue(maxsize=self.capacity)
            st.last_seen[channel] = time.monotonic()
        st.groups.setdefault(group, {})[channel] = time.monotonic()
        st.member_of.setdefault(channel, set()).add(group)

    async def group_discard(self, group, channel):
        st = self._state()
        members = st.groups.get(group)
        if members:
            members.pop(channel, None)
            if not members:
                st.groups.pop(group, None)
        mine = st.member_of.get(channel)
        if mine is not None:
            mine.discard(group)
            if not mine:
                st.member_of.pop(channel, None)

    async def flush(self):
        st = self._state()
        st.queues.clear()
        st.groups.clear()
        st.waiting.clear()
        st.last_seen.clear()
        st.member_of.clear()
