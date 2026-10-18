import base64
import json
import os
import secrets
import time

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from federation import protocol
from federation.models import Identity, Contact

try:
    import requests
except ImportError:
    requests = None
try:
    import websocket
except ImportError:
    websocket = None

OK = "\033[92mok\033[0m"
NO = "\033[91mFAIL\033[0m"
WARN = "\033[93m!\033[0m"


class Command(BaseCommand):
    help = "End-to-end deployment test: multi-process, cross-worker WS and throttle."

    def add_arguments(self, p):
        p.add_argument("--url", default=os.environ.get(
            "HOPLITA_SMOKE_URL", "http://127.0.0.1:8600"),
            help="Base URL of the already running server.")
        p.add_argument("--rounds", type=int, default=6,
                       help="Presence cycles (more = less room for luck).")
        p.add_argument("--keep", action="store_true",
                       help="Do not delete the test identities.")

    def _ws_url(self, base):
        return base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/fed/"

    def _mk_identity(self, tag):
        handle = f"smoke-{tag}-{secrets.token_hex(3)}"
        user = User.objects.create_user(username=handle, email=f"{handle}@smoke.local")
        ident = Identity.objects.create(
            user=user, handle=handle,
            public_key=base64.b64encode(secrets.token_bytes(32)).decode())
        token, _ = Token.objects.get_or_create(user=user)
        return ident, token.key

    def _connect(self, base, token, timeout=10):
        ws = websocket.create_connection(self._ws_url(base), timeout=timeout)
        ws.send(json.dumps({"op": "auth", "token": token, "req_id": "smoke",
                            "proto": protocol.PROTO,
                            "caps": list(protocol.CAPS)}))
        first = json.loads(ws.recv())
        if not first.get("ok"):
            raise CommandError(f"auth rejected: {first}")
        return ws

    def _drain_for(self, ws, event, seconds):
        end = time.time() + seconds
        while time.time() < end:
            ws.settimeout(max(0.2, end - time.time()))
            try:
                msg = json.loads(ws.recv())
            except Exception:
                return None
            if msg.get("event") == event:
                return msg
        return None

    def handle(self, *a, **o):
        if requests is None or websocket is None:
            raise CommandError("missing deps: pip install requests websocket-client")
        base = o["url"].rstrip("/")
        failures = []
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nHOPLITA FED — smoke against {base}\n"))

        pids, info = set(), {}
        for _ in range(24):
            try:
                r = requests.get(f"{base}/health", timeout=5)
                info = r.json()
                pids.add(info.get("pid"))
            except Exception as e:
                _hint = ""
                try:
                    if r.status_code in (401, 403, 404):
                        _hint = (f"\n\nThe URL answered HTTP {r.status_code} "
                                 f"with no JSON: a proxy in front is hiding "
                                 f"/health (the shipped Caddyfile does exactly "
                                 f"that — it is a fingerprint of the stack and "
                                 f"it answers to anybody).\nRun this AGAINST "
                                 f"THE APP, on the machine itself:\n"
                                 f"    manage.py fed_smoke --url "
                                 f"http://127.0.0.1:8600")
                except NameError:
                    _hint = ("\n\nNothing answered at all: is the service up? "
                             "`systemctl status hoplita-fed`")
                raise CommandError(f"/health does not answer: {e}{_hint}")
        n = len(pids)
        self.stdout.write(f" 1. processes seen: {n}  (PIDs {sorted(pids)})")
        try:
            _al = requests.get(f"{base}/admin/login/", timeout=8)
            if _al.status_code == 200:
                self.stdout.write("    admin login  : ok (200)")
            else:
                self.stdout.write(self.style.ERROR(
                    f"    admin login  : HTTP {_al.status_code} — the admin is "
                    f"DOWN. Almost always a migration that did not run: "
                    f"`manage.py migrate` (the theme has its own tables)."))
        except Exception as _e:
            self.stdout.write(self.style.ERROR(
                f"    admin login  : does not answer ({_e})"))
        try:
            _pr = requests.get(f"{base}/api/fed/protocol", timeout=5).json()
            self.stdout.write(f"    protocol      : {_pr.get('proto')} "
                              f"(serves from {_pr.get('min_proto')}; "
                              f"caps {', '.join(_pr.get('caps') or []) or 'none'})")
        except Exception as _e:
            self.stdout.write(self.style.ERROR(
                f"    protocol      : /api/fed/protocol does not answer ({_e})"))
        self.stdout.write(f"    channel layer : {info.get('channel_layer')}")
        self.stdout.write(f"    cache         : {info.get('cache', {}).get('backend')}")
        self.stdout.write(f"    database      : {info.get('db', {}).get('engine')}")
        if info.get("debug"):
            self.stdout.write(f"    {WARN} DEBUG is ON")
        if n == 1:
            self.stdout.write(f"    {WARN} single worker: the cross-process test will "
                              "pass trivially. Start with WEB_CONCURRENCY>1 for it to mean something.")

        a_id, a_tok = self._mk_identity("a")
        b_id, b_tok = self._mk_identity("b")
        Contact.objects.create(owner=a_id, peer=b_id, status="accepted")
        Contact.objects.create(owner=b_id, peer=a_id, status="accepted")
        rounds = max(1, o["rounds"])
        got = 0
        live_got = 0
        try:
            ws_b = self._connect(base, b_tok)
            for i in range(rounds):
                ws_a = self._connect(base, a_tok)
                ev = self._drain_for(ws_b, "presence", 6)
                if ev and ev.get("data", {}).get("handle") == a_id.handle:
                    got += 1
                ws_a.send(json.dumps({"op": "live", "params": {
                    "to": b_id.handle, "data": {"smoke": i}}}))
                lv = self._drain_for(ws_b, "live", 6)
                if lv and (lv.get("data") or {}).get("data") == {"smoke": i}:
                    live_got += 1
                try:
                    ws_a.close()
                except Exception:
                    pass
                self._drain_for(ws_b, "presence", 3)
            ws_b.close()
        finally:
            if not o["keep"]:
                for ident in (a_id, b_id):
                    u = ident.user
                    ident.delete()
                    u.delete()

        good = got == rounds
        self.stdout.write(f"\n 2. cross-worker presence: {got}/{rounds} received "
                          f"{OK if good else NO}")
        live_good = live_got == rounds
        self.stdout.write(f"    cross-worker live lane: {live_got}/{rounds} received "
                          f"{OK if live_good else NO}")
        if not live_good:
            failures.append(
                f"only {live_got} of {rounds} live messages arrived: the lane "
                "rides the channel layer, so this is the bus, or a server "
                "older than protocol 2 behind this URL.")
        if not good:
            failures.append(
                f"only {got} of {rounds} presence events arrived. With {n} workers "
                "that is exactly what happens when the channel layer is not Redis: "
                "the sockets land on different processes and group_send does not "
                "cross. Check HOPLITA_REDIS_URL.")
        elif n > 1:
            self.stdout.write(f"    {OK} with {n} processes, the odds of this passing "
                              f"by luck are 1/{n}^{rounds}")

        self.stdout.write("\n 3. throttle shared across workers...")
        c_id, c_tok = self._mk_identity("c")
        hdr = {"Authorization": f"Token {c_tok}", "X-Hoplita-Proto": str(protocol.PROTO)}
        codes = []
        try:
            for _ in range(120):
                try:
                    codes.append(requests.get(f"{base}/api/fed/contacts",
                                              headers=hdr, timeout=5).status_code)
                except Exception:
                    break
        finally:
            if not o["keep"]:
                u = c_id.user
                c_id.delete()
                u.delete()
        n429 = codes.count(429)
        if n429:
            self.stdout.write(f"    {OK} 429 after {codes.index(429)} requests "
                              f"({n429} of {len(codes)} throttled)")
        else:
            self.stdout.write(f"    {WARN} no 429 in {len(codes)} requests")
            if n > 1 and "locmem" in str(info.get("cache", {}).get("backend", "")).lower():
                failures.append(
                    f"local cache with {n} workers: the throttle counts per process, "
                    f"so the real limit is {n} times the one you configured.")
            else:
                self.stdout.write("      (can be normal if the tier allows more "
                                  "than 120 requests per minute)")

        self.stdout.write("")
        if failures:
            for f in failures:
                self.stdout.write(self.style.ERROR(f" {NO} {f}"))
            raise CommandError("smoke FAILED: this deployment is not multi-process safe.")
        self.stdout.write(self.style.SUCCESS(
            f" {OK} deployment is sound: {n} process(es), events crossing and "
            "shared limits.\n"))
