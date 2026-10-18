
import os

PROTO = 2


def _min_proto():
    try:
        return max(1, min(PROTO, int(os.environ.get("HOPLITA_MIN_PROTO") or 1)))
    except ValueError:
        return 1


MIN_PROTO = _min_proto()
CAPS = ("live",)

LIVE_MAX_BYTES = 6000

LIVE_RATE_PER_S = 50
LIVE_BURST = 200

LIVE_PEER_TTL_S = 60


def hello():
    return {"proto": PROTO, "min_proto": MIN_PROTO, "caps": list(CAPS),
            "live_max_bytes": LIVE_MAX_BYTES}


def client_proto(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def too_old(proto):
    if proto >= MIN_PROTO:
        return None
    return {"error": "client_too_old", "proto": PROTO, "min_proto": MIN_PROTO,
            "message": (f"This server speaks protocol {MIN_PROTO} to {PROTO} "
                        f"and your HOPLITA speaks {proto}. Update HOPLITA "
                        f"(install.py --update) and it will connect again.")}
