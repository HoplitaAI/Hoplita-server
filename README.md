# Hoplita server

The federation server for [HOPLITA](https://github.com/HoplitaAI/Hoplita):
accounts, contacts, presence, and the relay that carries what users share with
each other — conversations, notes folders, task projects, code, and the
encrypted snapshot that keeps a user's own machines in sync.

**It is a relay that cannot read what it carries.** Everything a user shares is
sealed on the client to the recipient's public key (Curve25519, libsodium
sealed boxes) before it leaves the machine; the server stores and forwards
opaque blobs. Contacts and shares are trusted because each user signs them
with their own Ed25519 key — the client checks those signatures itself, so a
server that lies about who is whose contact is caught, not believed. What a
server holds is identities, public keys, the contact graph, undelivered mail
and one sealed snapshot per user: losing it is an outage, not a leak.

---

## What it does

- **Accounts.** Sign-up with an email PIN (valid 15 minutes), then login by
  **proof of private key**: the server seals a random nonce to the account's
  public key and only the holder of the private key can return it (the
  challenge lives 2 minutes). An account registered with a password can also
  log in with handle and password. Sign-up is **invite-only by
  default** (codes in the environment) or open, by the operator's choice.
- **Contacts and presence.** Contact requests, acceptance, per-contact
  permissions, removal. Presence is real: a contact is online while their
  WebSocket is open, not "seen in the last five minutes".
- **The relay.** Envelopes (sealed messages, shares, code transfers, agent
  requests) go into the recipient's mailbox and are pushed over the WebSocket
  the moment they arrive; the client acknowledges what it has stored and the
  server forgets it. Undelivered mail expires after a TTL (30 days by default).
- **The live lane.** Small messages from one socket straight to the open
  sockets of a contact, through the bus, with nothing stored: a remote
  session's progress reaches the other screen in milliseconds instead of
  waiting in the mailbox behind everything else. What must arrive (the reply,
  a tool to run, a stop) still goes by the mailbox too.
- **A versioned protocol.** Clients and server say which protocol they speak
  and what they can do (`GET /api/fed/protocol`, the socket's auth, the
  `X-Hoplita-Proto` header). New features are used only when both ends have
  them, so old and new clients share one server; the operator can turn away
  clients too old to work with `HOPLITA_MIN_PROTO`, and they are told to
  update.
- **Shared and public folders.** Share records with a signed grant per member,
  read-only shares, and public folders anyone can subscribe to.
- **Own-machine sync.** One sealed snapshot per user, versioned, so several
  machines of the same person converge.
- **Children.** An owner mints a single-use enrollment token (valid 60 minutes);
  a headless machine redeems it and becomes a child identity with a mutual
  contact to its owner. Revoking it deletes the identity, its contacts, its
  mail and its session token; a socket it already had open is refused and
  closed on its next operation or keepalive.
- **Quotas.** Per-group limits — snapshot size, undelivered mail (count and
  bytes), contacts, children, API requests per minute — with per-user
  overrides. `0` means unlimited.
- **Operator tools.** An admin behind TOTP with a statistics page, a MOTD and
  server notices (broadcast or to one user), quota groups, banned emails, and
  a `/health` endpoint for probes.

## Architecture

```
 HOPLITA client ──HTTPS──▶ Caddy / Apache (TLS) ──▶ gunicorn ─┬─ uvicorn worker ─┐
 HOPLITA client ──WSS────▶        :443                :8600   ├─ uvicorn worker ─┼─▶ PostgreSQL
                                                              └─ uvicorn worker ─┘   (data + bus + cache)
```

- **Django + Django REST Framework** for the API (`/api/fed/…`, token
  authentication), **Django Channels** for the WebSocket (`/ws/fed/`). Both are
  served by the same ASGI application on **one port, 8600**.
- **Development**: one `daphne` process, SQLite, an in-memory channel layer.
  With a single process that is the correct setup, not a lesser one.
- **Production**: `gunicorn` as the process manager for **uvicorn ASGI
  workers** (this is not gunicorn-WSGI, which cannot serve WebSockets), under
  systemd, behind a TLS proxy.
- **PostgreSQL is the only stateful service.** It holds the data, and it is
  also:
  - **the channel layer** between workers (`federation/pg_layer.py`, built on
    `LISTEN`/`NOTIFY`). Events from one user to another travel through
    `group_send`, and that call does not cross processes by itself: without a
    shared bus, a user attached to worker A never hears an event emitted on
    worker B — silently;
  - **the cache** that holds the rate-limit counters (Django's database cache,
    table `hoplita_cache`), so the limit is the configured one and not *N*
    times it with *N* workers.
- **Redis is not needed.** `HOPLITA_REDIS_URL` still switches the channel layer
  and the cache to Redis, for operators who prefer it; it needs
  `channels-redis` and `redis` installed by hand.
- **Static files** (the admin's CSS and icons) are served by the application
  itself through WhiteNoise, so the admin looks the same whatever proxy is in
  front of it. The admin uses the Unfold theme and `django-otp` for TOTP.

### It refuses to start when the setup is wrong

`hoplitafed/boot_checks.py` aborts start-up, with the reason, when:

- there is more than one worker and the channel layer is in memory, the cache
  is local, or the database is SQLite;
- `DEBUG` is off and `HOPLITA_SECRET_KEY` is missing or is the development key,
  or `HOPLITA_ALLOWED_HOSTS` is empty;
- a model has no migration.

It warns (without aborting) when open registration is on in production, when
`ALLOWED_HOSTS` is `*`, and when `workers × (HTTP concurrency + WS pool + 2) + 5`
database connections would exceed Postgres' `max_connections`. The worker
count is read from `WEB_CONCURRENCY` (or `HOPLITA_WORKERS`, `GUNICORN_WORKERS`).

### What bounds one worker

| Limit | Variable | Default |
|---|---|---|
| HTTP requests served at once; the rest wait on the event loop | `HOPLITA_HTTP_CONCURRENCY` | 12 |
| Seconds a request may wait before a `503` with `Retry-After` | `HOPLITA_HTTP_QUEUE_S` | 30 |
| Threads (and database connections) for WebSocket operations | `HOPLITA_WS_POOL` | 8 |
| Live-lane messages per socket: size / rate / burst (no database, not counted in requests per minute) | — | 6000 B / 50 per s / 200 |
| Oldest client protocol served | `HOPLITA_MIN_PROTO` | 1 |
| Bus connections (one `LISTEN`, one `NOTIFY`) | — | 2 |
| Mailbox page: envelopes / body bytes per request (HOPLITA clients ask for pages) | `HOPLITA_INBOX_PAGE_MAX` / `HOPLITA_INBOX_PAGE_BYTES` | 200 / 4 MB |
| Mailbox page for a client that does not ask for pages | `HOPLITA_INBOX_LEGACY_PAGE_BYTES` | 64 MB |
| Largest envelope | `HOPLITA_MAX_ENVELOPE_BYTES` | 4 MB |
| Largest request body | `HOPLITA_MAX_BODY_BYTES` | 32 MB |

Workers are recycled every ~2000 requests (jittered), and the unit tells
glibc's allocator to return freed memory (`MALLOC_ARENA_MAX=2` and the mmap /
trim thresholds). Budget a few hundred MB of RAM per worker and set
`WEB_CONCURRENCY` to what the machine holds.

---

## Run it for development

Python 3.11 or newer.

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
PY=./venv/bin/python ./run.sh
```

`run.sh` applies the migrations and starts one `daphne` process on
`0.0.0.0:8600` with `DEBUG` on and SQLite (`db.sqlite3` next to `manage.py`).
Under `DEBUG` a development secret key is used, and the registration response
includes the email PIN so you can sign up without a mail server.

Point a HOPLITA client at it when it registers: the server URL is given once,
at sign-up (`http://<this machine>:8600`), and the client keeps it with its
identity. `run.sh` only accepts the host names `localhost` and `127.0.0.1`;
for clients on other machines add the name or address they use:
`HOPLITA_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.20 ./run.sh`. Users federate with the people on the same server, so a group that
runs its own server registers there. The client side is explained in
[HOPLITA's federation guide](https://github.com/HoplitaAI/Hoplita/blob/main/docs/FEDERATION.md).

Development sign-up is still invite-only unless you say otherwise:

```bash
HOPLITA_OPEN_REGISTRATION=1 PY=./venv/bin/python ./run.sh
# or
HOPLITA_INVITE_CODES=let-me-in PY=./venv/bin/python ./run.sh
```

### Production-shaped, on your machine

```bash
export HOPLITA_DB_NAME=hoplitafed HOPLITA_DB_USER=hoplita HOPLITA_DB_PASSWORD=...
PY=./venv/bin/python ./run.sh --prod-local
```

The production process model — gunicorn with `WEB_CONCURRENCY` (default 4)
uvicorn workers, the Postgres bus and cache, the allocator settings — against
a local PostgreSQL, so the multi-process setup is tested instead of assumed.
It applies the migrations and creates the cache table; it does not collect
static files (run `manage.py collectstatic` for a styled admin with `DEBUG`
off), and it listens on `0.0.0.0:8600`.

---

## Deploy

What a production install is made of, all in `deploy/`:

| File | What it is |
|---|---|
| `hoplita-fed.env.example` | The settings, as environment variables. Copied to `/etc/hoplita-fed.env` (mode 600). |
| `hoplita-fed.service` | The systemd unit: user `hoplita`, code in `/opt/hoplita-fed`, gunicorn + uvicorn on `127.0.0.1:8600`. Before the workers start it runs `migrate`, `collectstatic`, `createcachetable` and `boot_sweep`. |
| `Caddyfile` | Caddy in front: TLS with automatic certificates (ACME), WebSockets, `/health` kept private, admin API off. |
| `apache-hoplita.conf` | The alternative: Apache with `mod_proxy_wstunnel` and a Let's Encrypt certificate. |
| `finish.sh` | Installs (or repairs) a whole server from a release tarball. |
| `accept.sh` | The acceptance gate: exits 0 only when the deployment really works. |

Use Caddy **or** Apache, not both: they would both want port 443.

### The one-command route (RHEL family)

`finish.sh` targets RHEL, Rocky Linux and AlmaLinux 9/10 (`dnf`, EPEL,
`postgresql-setup`). As root, with a tarball of this repository:

```bash
git archive --format=tar.gz --prefix=hoplita-fed/ -o /tmp/hoplita-fed.tgz HEAD
# copy it to the server, then on the server:
sudo TGZ=/tmp/hoplita-fed.tgz DOMAIN=fed.example.com bash finish.sh
```

It is idempotent — running it twice is running it once, and running it on a
half-finished machine finishes it. In order, it:

1. enables EPEL and CRB and installs `python3`, `python3-devel`, `gcc`,
   `postgresql-server`, `postfix` and `caddy`;
2. creates the `hoplita` system user and unpacks the code into
   `/opt/hoplita-fed` (`PREFIX=` changes it, and the unit follows);
3. creates the virtual environment and installs `requirements.txt`;
4. initialises PostgreSQL if needed, switches local TCP logins to
   `scram-sha-256`, and creates the `hoplita` role and the `hoplitafed`
   database with a generated password;
5. enables postfix;
6. writes `/etc/hoplita-fed.env` from scratch — it carries over the existing
   secret key, database password and invitation code (generating them on the
   first run), and **everything else is set again to the script's values**:
   4 workers, invite-only, and the mail settings from the `MAIL_*` variables
   (local postfix when they are not given). Pass the same variables on every
   run, and re-apply any setting you added by hand;
7. installs and starts the systemd unit;
8. writes `/etc/caddy/Caddyfile` for `DOMAIN` and `www.DOMAIN` (both names
   must resolve to the machine, for the certificates) and opens http/https in
   firewalld;
9. runs the acceptance gate and, when it passes, prints the invitation code
   (it is also in `HOPLITA_INVITE_CODES` in the env file).

Other knobs: `ENVFILE` (default `/etc/hoplita-fed.env`), `PGDATA`, `DBNAME`
(`hoplitafed`), `DBUSER` (`hoplita`).

Mail is a decision, not a default. Sign-up mails a PIN and login waits until it
is used: no mail, no users. A local postfix that delivers straight to the
recipients' MX needs outbound port 25, reverse DNS matching your domain, SPF
and DKIM; many hosting providers block port 25. To relay through a provider:

```bash
sudo MAIL_HOST=smtp.provider.com MAIL_PORT=587 MAIL_TLS=1 \
     MAIL_USER=... MAIL_PASS=... MAIL_FROM=no-reply@fed.example.com \
     TGZ=/tmp/hoplita-fed.tgz DOMAIN=fed.example.com bash finish.sh
```

### By hand (any Linux)

1. **PostgreSQL**: a database and a role that owns it.

   ```sql
   CREATE ROLE hoplita LOGIN PASSWORD '...';
   CREATE DATABASE hoplitafed OWNER hoplita;
   ```

2. **The code and its environment**:

   ```bash
   sudo useradd --system --home-dir /opt/hoplita-fed --shell /sbin/nologin hoplita
   sudo mkdir -p /opt/hoplita-fed && sudo cp -r . /opt/hoplita-fed/
   sudo python3 -m venv /opt/hoplita-fed/venv
   sudo /opt/hoplita-fed/venv/bin/pip install -r /opt/hoplita-fed/requirements.txt
   sudo chown -R hoplita:hoplita /opt/hoplita-fed
   ```

3. **The settings**:

   ```bash
   sudo cp deploy/hoplita-fed.env.example /etc/hoplita-fed.env
   sudo chmod 600 /etc/hoplita-fed.env
   python3 -c "import secrets;print(secrets.token_urlsafe(64))"   # the secret key
   ```

   Fill in at least `HOPLITA_SECRET_KEY`, `HOPLITA_ALLOWED_HOSTS`,
   `HOPLITA_CSRF_ORIGINS`, the `HOPLITA_DB_*` block, the mail block and either
   `HOPLITA_INVITE_CODES` or `HOPLITA_OPEN_REGISTRATION=1` (see
   [Configuration](#configuration)). Keep `127.0.0.1` and `localhost` in
   `HOPLITA_ALLOWED_HOSTS`: the health probes and the acceptance gate talk to
   the application directly.

4. **The service**:

   ```bash
   sudo cp deploy/hoplita-fed.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now hoplita-fed
   journalctl -u hoplita-fed -f
   ```

   systemd reads the unit from `/etc/systemd/system/`: after changing the one
   in `deploy/`, copy it again and `daemon-reload`, or the old one keeps
   running.

5. **TLS in front.** With Caddy, replace `example.com` with your domain:

   ```bash
   sed "s/example\.com/fed.example.com/g" deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile
   sudo systemctl restart caddy
   ```

   Caddy obtains and renews the certificates itself (for the name and its
   `www.`); the names must resolve to the machine and ports 80 and 443 must be reachable (80 answers the ACME
   challenge). The Caddyfile turns Caddy's admin API off, so use `restart`, not
   `reload`. With Apache, copy `deploy/apache-hoplita.conf`, set your
   `ServerName`, `ServerAlias` and certificate paths, and enable `proxy`,
   `proxy_http`, `proxy_wstunnel`, `headers`, `alias` and `ssl`; the `/ws/`
   `ProxyPass` must come before the generic one or the WebSocket never
   upgrades. Both configurations keep idle sockets open for an hour and keep
   `/health` off the public site.

6. **An admin account** (the admin requires TOTP):

   As root (the env file is readable only by root):

   ```bash
   cd /opt/hoplita-fed
   set -a; . /etc/hoplita-fed.env; set +a
   sudo -u hoplita -E ./venv/bin/python manage.py add_admin alice --email you@example.com
   ```

   It prints an `otpauth://` URL; paste it into any authenticator app. Run it
   again to print it again, `--rotate` for a new secret, `--password` to set
   the password without a prompt. The admin is at `https://<domain>/admin/`.

7. **Check it** — see [Is it working?](#is-it-working).

> **`manage.py` by hand needs the environment.** systemd hands the service
> `/etc/hoplita-fed.env`; a bare `./venv/bin/python manage.py …` gets none of
> it, falls back to SQLite and quietly creates a second, empty database. Load
> the file first, as root, as above (`set -a; . /etc/hoplita-fed.env; set +a`).

### Is it working?

```bash
sudo DOMAIN=fed.example.com /opt/hoplita-fed/deploy/accept.sh
```

The gate exits 0 only when all of this holds:

1. the service is active and enabled;
2. it runs gunicorn with uvicorn workers, not the development server;
3. `/health` answers, and with `WEB_CONCURRENCY` above 1 more than one worker
   answers it;
4. `DEBUG` is off;
5. a Postgres `NOTIFY` reaches a `LISTEN` (the bus between workers);
6. the database is PostgreSQL (`EXPECT_DB=sqlite` accepts SQLite) and no
   migration is pending;
7. mail is configured: `HOPLITA_EMAIL_HOST` is set and its port accepts a
   connection, and a relaying postfix has an empty queue (`MAIL_PROBE=<address>`
   also sends a real message);
8. with `DOMAIN=`: TLS works from outside, plain http redirects to https, and
   `/health` answers 404 from outside;
9. `fed_smoke` passes: WebSocket events cross between workers and the rate
   limit is shared;
10. nothing listens on a public interface except the ports in `PUBLIC_OK`
    (default `22 80 443`).

`LOCAL_URL`, `SERVICE`, `PREFIX` and `ENVFILE` point it at a different install.

`fed_smoke` can also run on its own, against the application rather than the
public URL:

```bash
./venv/bin/python manage.py fed_smoke --url http://127.0.0.1:8600
```

`/health` (unauthenticated, kept off the public site by both proxy
configurations) returns the worker's PID, its uptime, whether `DEBUG` is on,
whether the setup is safe for several processes, and the database, cache and
channel-layer backends in use; it answers `503` when the database or the cache
fails:

```bash
curl -s http://127.0.0.1:8600/health | python3 -m json.tool
```

### Upgrading

```bash
# on the server, with the new tarball unpacked in /tmp/hoplita-fed
set -a; . /etc/hoplita-fed.env; set +a
sudo -u postgres pg_dump -Fc "$HOPLITA_DB_NAME" > /root/hoplita-fed-$(date +%F).dump
rsync -a --delete --exclude venv --exclude staticfiles --exclude db.sqlite3 \
      --exclude __pycache__ /tmp/hoplita-fed/ /opt/hoplita-fed/
chown -R hoplita:hoplita /opt/hoplita-fed
/opt/hoplita-fed/venv/bin/pip install -r /opt/hoplita-fed/requirements.txt
cp /opt/hoplita-fed/deploy/hoplita-fed.service /etc/systemd/system/
systemctl daemon-reload && systemctl restart hoplita-fed
DOMAIN=fed.example.com /opt/hoplita-fed/deploy/accept.sh
```

Migrations, static files, the cache table and the boot sweep run by themselves
before the workers start. A restart drops every open WebSocket; clients
reconnect within a minute, with jitter.

### Backups

The database is everything: identities, public keys, contacts, mail in flight
and the sealed snapshots. Losing it means every user registers again and loses
their server-side copy.

```bash
pg_dump -Fc hoplitafed > /backups/fed-$(date +%F).dump                 # PostgreSQL
sqlite3 db.sqlite3 ".backup '/backups/fed-$(date +%F).sqlite3'"       # SQLite
```

Test the restore.

---

## Configuration

Every setting is an environment variable; the defaults are the safe ones
(`DEBUG` off, no hosts allowed, registration closed).

**Deployment**

| Variable | Default | Meaning |
|---|---|---|
| `HOPLITA_SECRET_KEY` | — | Django's secret key. Required without `DEBUG`. Changing it logs everyone out of the admin. |
| `HOPLITA_DEBUG` | `0` | Development mode. Never on a public server. |
| `HOPLITA_ALLOWED_HOSTS` | (none) | Comma-separated host names the server answers to. Include `127.0.0.1,localhost`. |
| `HOPLITA_CSRF_ORIGINS` | (none) | `https://` origins trusted for the admin's forms. |
| `HOPLITA_BEHIND_PROXY` | `1` | Trust the proxy's `X-Forwarded-Proto` / `X-Forwarded-Host`. |
| `HOPLITA_HSTS_SECONDS` | `0` | HSTS; turn it on only once TLS works (31536000 = one year). |
| `HOPLITA_LOG_LEVEL` | `INFO` | Log level; logs go to stdout (the journal). |

**Processes and limits**

| Variable | Default | Meaning |
|---|---|---|
| `WEB_CONCURRENCY` | 1 | gunicorn workers. More than one requires PostgreSQL (or Redis). |
| `HOPLITA_HTTP_CONCURRENCY` | 12 | HTTP requests served at once per worker. |
| `HOPLITA_HTTP_QUEUE_S` | 30 | Seconds a request waits for a slot before a `503`. |
| `HOPLITA_WS_POOL` | 8 | Threads per worker for WebSocket operations. |
| `HOPLITA_CHANNEL_CAPACITY` | 1500 | Per-channel queue of the bus; absorbs the reconnect burst after a restart. |
| `HOPLITA_MAX_BODY_BYTES` | 33554432 | Largest request body. |
| `HOPLITA_MAX_ENVELOPE_BYTES` | 4000000 | Largest envelope. |
| `HOPLITA_INBOX_PAGE_MAX` | 200 | Envelopes per mailbox page. |
| `HOPLITA_INBOX_PAGE_BYTES` | = max envelope | Body bytes per mailbox page. |
| `HOPLITA_MAX_IDS_PER_CALL` | 1000 | Ids per acknowledge / withdraw / fetch call. |
| `HOPLITA_MAIL_TTL_DAYS` | 30 | Undelivered mail older than this is deleted (`0` = never). |

**Database**

| Variable | Default | Meaning |
|---|---|---|
| `HOPLITA_DB_NAME` | — | Set it to use PostgreSQL; unset means SQLite (`db.sqlite3`, WAL). |
| `HOPLITA_DB_USER` | `hoplita` | |
| `HOPLITA_DB_PASSWORD` | — | |
| `HOPLITA_DB_HOST` | `127.0.0.1` | |
| `HOPLITA_DB_PORT` | `5432` | |
| `HOPLITA_DB_CONN_MAX_AGE` | `0` | Keep it at 0: under ASGI Django keeps a connection per *thread*, and a longer life exhausts Postgres' slots. |
| `HOPLITA_DB_POOL` | `0` | `1` turns on psycopg's connection pool (needs `pip install "psycopg[pool]"`). |
| `HOPLITA_DB_POOL_MIN` / `_MAX` | 1 / 8 | Pool size. |
| `HOPLITA_REDIS_URL` | — | Use Redis for the bus and the cache instead of PostgreSQL. |

SQLite is fine for one process and a few hundred users; it allows one writer,
and the reconnect burst after a restart writes everyone's presence at once.
The bus holds two direct connections to Postgres per worker: behind
pgbouncer, those two must use session pooling, because transaction pooling
drops `LISTEN`.

**Sign-up and mail**

| Variable | Default | Meaning |
|---|---|---|
| `HOPLITA_INVITE_CODES` | — | Comma-separated invitation codes. Invite-only with no code set means **nobody** can sign up. |
| `HOPLITA_OPEN_REGISTRATION` | `0` | `1` lets anyone sign up. |
| `HOPLITA_EXPOSE_DEV_PIN` | = `DEBUG` | Return the email PIN in the registration response (development only). |
| `HOPLITA_EMAIL_HOST` | — | SMTP server; unset prints mail to the log instead of sending it. |
| `HOPLITA_EMAIL_PORT` | 25 | |
| `HOPLITA_EMAIL_USE_TLS` | `0` | |
| `HOPLITA_EMAIL_USER` / `_PASSWORD` | — | |
| `HOPLITA_EMAIL_FROM` | `no-reply@localhost` | Sender address of the PIN mail. |

A client enters an invitation code when it registers (the `invite` argument of
`fed_register`). Codes are not used up: anyone holding one can sign up until
it is removed from the list.

---

## Operating it

### The admin

`/admin/`, behind username, password and a TOTP code. From the sidebar:

- **Statistics** (`/admin/stats/`): users alive, asleep and gone, mail in
  flight, and 14 days of activity.
- **MOTD and notices**: a message of the day for everyone, or a notice to one
  user, with a level and an optional expiry. Clients show it in their own
  language when it carries a machine code.
- **Users**: each identity with its quota group, and per-user overrides for
  mail in flight (MB and envelopes), contacts and the sync snapshot (MB).
- **Quota groups**: the limits of each group; the one marked default applies to
  anyone without a group. Three come with the migrations:

  | Group | Snapshot | Mail (count / bytes) | Contacts | Children | Requests/min |
  |---|---|---|---|---|---|
  | `nobody` (default) | 5 MB | 200 / 10 MB | 20 | 2 | 300 |
  | `power` | 100 MB | 5000 / 200 MB | 500 | 50 | 600 |
  | `vip` | unlimited | unlimited | unlimited | unlimited | unlimited |

  The mail limits count what a user has *sent* that is still waiting to be
  collected, so nobody can fill somebody else's mailbox to cut them off.
- **Contacts, envelopes, public folders** — for inspection.
- **Banned emails**: addresses that cannot register; an action deactivates the
  accounts already using them.

### Management commands

Run them with the environment loaded (see above).

| Command | What it does |
|---|---|
| `add_admin <user> [--email] [--password] [--rotate]` | Creates or refreshes an admin account with its TOTP device and prints the `otpauth://` URL. |
| `fed_smoke --url <url> [--rounds N] [--keep]` (or `HOPLITA_SMOKE_URL`) | End-to-end check of a running server: distinct worker PIDs, WebSocket events across workers, a shared rate limit. Exits non-zero with the reason. |
| `sweep_mail [--days N] [--dry-run]` | Deletes undelivered mail older than the TTL and says how much it freed. Suitable for cron; it also runs at every start. |
| `boot_sweep` | Clears presence left by the previous run and sweeps stale mail. The unit runs it once before the workers start. |

### When to grow

- **CPU pinned or latency rising** → more workers (`WEB_CONCURRENCY`), which
  needs PostgreSQL.
- **Many WebSockets** → each one is a file descriptor; the unit sets
  `LimitNOFILE=65535`.
- **`database is locked` in the log** → SQLite asking for PostgreSQL.
- **Workers killed by the OOM killer** → more workers than the RAM holds, not
  more users than the server can serve; lower `WEB_CONCURRENCY` or uncomment
  `MemoryHigh` / `MemoryMax` in the unit.
- **`remaining connection slots are reserved`** → too many database
  connections: check `HOPLITA_DB_CONN_MAX_AGE=0`, lower the per-worker limits,
  raise `max_connections`, or use the pool.

An idle client with its socket up makes about one request a minute; without a
socket (REST polling) about twelve. The requests-per-minute limit counts
WebSocket operations and REST calls together; unauthenticated endpoints are
not counted, and if the cache fails the limiter lets requests through for 30
seconds rather than blocking everyone.

### The front page

`/` is the HOPLITA landing page: `hoplitafed/landing.html`, with its
stylesheet, script, three.js, its typeface (Cinzel, SIL OFL, licence beside it)
and screenshots under `federation/static/landing/`, served by WhiteNoise after `collectstatic` (the
unit runs it on every start). It sets no cookie and makes no request off the
host. `robots.txt` keeps `/api/`, `/ws/` and `/admin/` out of indexes. To
replace the page, edit those files; if `landing.html` is missing, `/` answers a
one-line page with the logo.

---

## API

All under `/api/fed/`, JSON, `Authorization: Token <token>`. `register`,
`verify`, `login/challenge`, `login/verify`, `login` and `spawn/redeem` need no
token.

| Area | Endpoints |
|---|---|
| Account | `register`, `verify`, `login/challenge`, `login/verify`, `login`, `heartbeat`, `deregister`, `quota` |
| Keys | `key/<handle>`, `signkey` |
| Contacts | `contacts`, `contacts/add`, `contacts/accept`, `contacts/attest`, `contacts/permissions`, `contacts/remove` |
| Mail | `envelope/send`, `inbox`, `inbox/ack`, `outbox/withdraw` |
| Folders | `folders`, `folders/share`, `folders/accept`, `folders/grant`, `folders/unshare`, `folders/publish`, `folders/unpublish`, `folders/subscribe`, `folders/unsubscribe`, `public-folders` |
| Own machines | `sync/pull`, `sync/push` |
| Children | `spawn/create`, `spawn/redeem`, `children`, `children/revoke` |
| Server | `motd`, `notices` |

The WebSocket at `/ws/fed/` authenticates with the same token (`{"op": "auth",
"token": …}`), then takes frames `{"op", "req_id", "params"}` for the everyday
operations — contacts, mail, folders, sync, quota, notices, under names such as
`list_contacts`, `send_envelope` and `fetch_inbox`, plus `ping` — and pushes
events (new mail, presence, notices) as they happen. Account, children and
withdraw operations are REST only. A socket whose token stops being valid is
closed with code 4401.

Errors carry an `error` field: a machine code for the cases the client
translates (`quota_exceeded`, `registration_closed`, `version_conflict`, …),
otherwise a short English sentence. Statuses worth knowing:

| Status | When |
|---|---|
| `402` | A quota is exceeded (`error: quota_exceeded`, with `resource`, `limit` and `used`). |
| `409` | Optimistic-concurrency conflict on `sync/push` (pull, merge, retry), or the thing already exists. |
| `413` | An envelope over the size limit (`envelope_too_big`). |
| `429` | The requests-per-minute limit. |
| `503` | The worker is at capacity (`busy`, `Retry-After: 5`). |

## License

GNU General Public License v3.0 — see `LICENSE`.
