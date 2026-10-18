from __future__ import annotations

import os
import sys


class BootError(RuntimeError):
    pass


def _workers() -> int:
    for var in ("WEB_CONCURRENCY", "HOPLITA_WORKERS", "GUNICORN_WORKERS"):
        v = os.environ.get(var, "").strip()
        if v.isdigit() and int(v) > 0:
            return int(v)
    return 1


def check(strict: bool = True) -> list[str]:
    from django.conf import settings

    problems: list[str] = []
    warnings: list[str] = []
    n = _workers()

    layer = (settings.CHANNEL_LAYERS.get("default", {}) or {}).get("BACKEND", "")
    cache = (settings.CACHES.get("default", {}) or {}).get("BACKEND", "")
    in_memory = "InMemoryChannelLayer" in layer
    loc_mem = "locmem" in cache

    if n > 1 and in_memory:
        problems.append(
            f"{n} workers with an in-memory CHANNEL_LAYERS. WebSocket events "
            "do NOT cross processes: set HOPLITA_DB_NAME (Postgres carries "
            "the bus, federation.pg_layer) or drop to 1 worker. "
            "HOPLITA_REDIS_URL still works as the legacy rollback.")
    if n > 1 and loc_mem:
        problems.append(
            f"{n} workers with a local cache (locmem). The per-tier throttle "
            f"would count per process, so the real limit would be {n}x the "
            "one you configured: set HOPLITA_DB_NAME (the cache rides the "
            "database) or HOPLITA_REDIS_URL (legacy).")

    engine = (settings.DATABASES.get("default", {}) or {}).get("ENGINE", "")
    if n > 1 and engine.endswith("sqlite3"):
        problems.append(
            f"{n} workers writing to SQLite. It allows ONE writer, so the "
            "processes queue behind a lock exactly when they are needed: set "
            "HOPLITA_DB_NAME (Postgres) or drop to 1 worker.")

    if engine.endswith("postgresql"):
        try:
            from hoplitafed.limits import HTTP_CONCURRENCY
            ws_pool = int(os.environ.get("HOPLITA_WS_POOL") or "8")
            need = n * (HTTP_CONCURRENCY + ws_pool + 2) + 5
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute("SHOW max_connections")
                have = int(cur.fetchone()[0])
            connection.close()
            if need > have:
                warnings.append(
                    f"{n} workers x (HTTP {HTTP_CONCURRENCY} + WS pool "
                    f"{ws_pool} + bus 2) = up to {need} database connections, "
                    f"and Postgres allows {have}. Lower WEB_CONCURRENCY or "
                    "HOPLITA_HTTP_CONCURRENCY, or raise max_connections.")
        except Exception as e:
            warnings.append(f"Could not size database connections: {e}")

    if not settings.DEBUG:
        if not settings.SECRET_KEY:
            problems.append("HOPLITA_SECRET_KEY is missing (required without DEBUG).")
        elif "django-insecure" in settings.SECRET_KEY:
            problems.append("HOPLITA_SECRET_KEY is the development one. Generate one: "
                            "python -c \"import secrets;print(secrets.token_urlsafe(64))\"")
        if not settings.ALLOWED_HOSTS:
            problems.append("HOPLITA_ALLOWED_HOSTS is missing (e.g. example.com).")
        elif "*" in settings.ALLOWED_HOSTS:
            warnings.append("ALLOWED_HOSTS=* accepts any Host header: set your domain.")
        try:
            from federation.models import RegistrationPolicy
            from django.db import connection
            mode = RegistrationPolicy.current().mode
            connection.close()
            if mode == RegistrationPolicy.OPEN:
                warnings.append("OPEN registration in production: anyone can "
                                "create identities (sign-up policy, admin).")
        except Exception:
            pass

    stale = [v for v in ("HOPLITA_INVITE_CODES", "HOPLITA_OPEN_REGISTRATION")
             if os.environ.get(v, "").strip()]
    if stale:
        warnings.append(f"{', '.join(stale)} no longer does anything: sign-up "
                        "and invitation codes are set in the admin (what was "
                        "here was brought over by migration 0020). Remove the "
                        "line(s) from the env file.")

    try:
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
        from django.db.migrations.state import ProjectState

        loader = MigrationLoader(None, ignore_no_migrations=True)
        auto = MigrationAutodetector(
            loader.project_state(), ProjectState.from_apps(_apps()),
            NonInteractiveMigrationQuestioner(specified_apps=set(), dry_run=True))
        changes = auto.changes(graph=loader.graph)
        if changes:
            apps = ", ".join(sorted(changes))
            problems.append(
                f"There are models with NO migration ({apps}). The table will "
                "not exist and it will fail at runtime: run manage.py makemigrations.")
    except Exception as e:
        warnings.append(f"Could not check for migration drift: {e}")

    for w in warnings:
        print(f"[BOOT][warning] {w}", file=sys.stderr)
    if problems and strict:
        msg = "\n".join(f"  x {p}" for p in problems)
        raise BootError(
            f"\n{'='*70}\nHOPLITA FED — invalid configuration, refusing to start:\n"
            f"{msg}\n{'='*70}\n")
    return problems


def _apps():
    from django.apps import apps
    return apps
