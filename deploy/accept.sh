#!/usr/bin/env bash

set -uo pipefail

PREFIX=${PREFIX:-/opt/hoplita-fed}
LOCAL_URL=${LOCAL_URL:-http://127.0.0.1:8600}
DOMAIN=${DOMAIN:-}
SERVICE=${SERVICE:-hoplita-fed}
ENVFILE=${ENVFILE:-/etc/hoplita-fed.env}

as_hoplita() {
	local envargs=()
	if [ -r "$ENVFILE" ]; then
		mapfile -t envargs < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENVFILE")
	fi
	sudo -u hoplita env "${envargs[@]}" "$@"
}

fails=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails + 1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

head_ "1. the service"
svc_up=0
systemctl is-active --quiet "$SERVICE" \
	&& { ok "$SERVICE is active"; svc_up=1; } || bad "$SERVICE is NOT active"
systemctl is-enabled --quiet "$SERVICE" \
	&& ok "enabled at boot" || bad "not enabled at boot (a reboot loses it)"

head_ "2. it is gunicorn+uvicorn, not daphne"
cmd=$(systemctl show -p ExecStart --value "$SERVICE" 2>/dev/null)
case "$cmd" in
*gunicorn*uvicorn.workers.UvicornWorker*)
	ok "gunicorn managing uvicorn ASGI workers" ;;
*daphne*) bad "still daphne: single process, no multi-worker" ;;
*) bad "unrecognised ExecStart: ${cmd:0:90}" ;;
esac

head_ "3. health, and how many workers really answer"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$LOCAL_URL/health")
[ "$code" = "200" ] && ok "/health -> 200" || bad "/health -> $code (old code has no /health)"
want=$(grep -oP '^WEB_CONCURRENCY=\K\d+' "$ENVFILE" 2>/dev/null || echo 1)
if [ "$code" = "200" ]; then
	pids=$(for _ in $(seq 1 $((want * 8))); do
		curl -s --max-time 5 "$LOCAL_URL/health"
		echo
	done | grep -oE '"pid"[: ]+[0-9]+' | grep -oE '[0-9]+' | sort -u | wc -l)
else
	pids=0
fi
if [ "$want" -gt 1 ]; then
	[ "$pids" -ge 2 ] \
		&& ok "$pids distinct worker PIDs answered (WEB_CONCURRENCY=$want)" \
		|| bad "only $pids PID answered with WEB_CONCURRENCY=$want — one worker is doing all the work"
else
	ok "WEB_CONCURRENCY=1, single worker on purpose"
fi

head_ "4. DEBUG is off"
body=$(curl -s --max-time 10 "$LOCAL_URL/definitely-not-a-real-path")
case "$body" in
*"You’re seeing this error because you have"*|*"DEBUG = True"*|*"Using the URLconf"*)
	bad "DEBUG is ON: the 404 page is leaking internals" ;;
*) ok "the 404 page leaks nothing" ;;
esac

head_ "6. the cross-process bus multi-worker depends on"
url=$(grep -oP '^HOPLITA_REDIS_URL=\K\S+' "$ENVFILE" 2>/dev/null || true)
dbn=$(grep -oP '^HOPLITA_DB_NAME=\K\S+' "$ENVFILE" 2>/dev/null || true)
if [ -n "$url" ]; then
	"$PREFIX/venv/bin/python" - "$url" <<-'PY' && ok "Redis (legacy rollback) answers PING" || bad "HOPLITA_REDIS_URL is set but Redis does NOT answer at $url"
		import sys, redis
		sys.exit(0 if redis.from_url(sys.argv[1]).ping() else 1)
	PY
elif [ -n "$dbn" ]; then
	(cd "$PREFIX" && set -a && . "$ENVFILE" && set +a && \
	 venv/bin/python - <<-'PY') && ok "Postgres bus: NOTIFY reached LISTEN" || bad "Postgres bus: LISTEN/NOTIFY did not cross"
		import os, sys, django
		os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hoplitafed.settings")
		django.setup()
		import psycopg
		from federation.pg_layer import _dsn_from_settings
		dsn = _dsn_from_settings()
		with psycopg.connect(dsn, autocommit=True) as ear, \
		     psycopg.connect(dsn, autocommit=True) as mouth:
		    ear.execute("LISTEN accept_probe")
		    mouth.execute("SELECT pg_notify('accept_probe', 'ping')")
		    got = None
		    for n in ear.notifies(timeout=5, stop_after=1):
		        got = n
		    sys.exit(0 if got and got.payload == "ping" else 1)
	PY
else
	[ "$want" -gt 1 ] \
		&& bad "$want workers with neither HOPLITA_DB_NAME nor HOPLITA_REDIS_URL: WS events would not cross processes" \
		|| ok "single worker: the in-memory layer is enough"
fi

head_ "7. the database is the one you meant"
engine=$(curl -s --max-time 5 "$LOCAL_URL/health" | grep -oP '"engine":\s*"\K[^"]+')
case "${EXPECT_DB:-postgresql}" in
"$engine"*|*"$engine") ok "database engine: $engine" ;;
*) bad "database engine is '$engine', expected '${EXPECT_DB:-postgresql}' — set HOPLITA_DB_NAME" ;;
esac

head_ "7b. no migration is pending"
if [ ! -x "$PREFIX/venv/bin/python" ]; then
	bad "no venv at $PREFIX/venv — nothing has been installed here yet"
elif as_hoplita "$PREFIX/venv/bin/python" "$PREFIX/manage.py" \
	migrate --check --noinput >/dev/null 2>&1; then
	ok "the database is at the latest migration"
else
	bad "migrations pending: run manage.py migrate"
fi

head_ "7c. someone can sign up"
if grep -qE '^HOPLITA_(INVITE_CODES|OPEN_REGISTRATION)=.' "$ENVFILE" 2>/dev/null; then
	bad "HOPLITA_INVITE_CODES / HOPLITA_OPEN_REGISTRATION in $ENVFILE do nothing now (sign-up is set in the admin): remove the line(s)"
fi
if [ -x "$PREFIX/venv/bin/python" ] && signup=$(as_hoplita "$PREFIX/venv/bin/python" \
	"$PREFIX/manage.py" signup 2>/dev/null); then
	mode=$(head -1 <<<"$signup")
	usable=$(grep -cE '^  [^ ]+ +usable ' <<<"$signup" || true)
	case "$mode" in
	*Invitation*)
		[ "$usable" -gt 0 ] && ok "by invitation, $usable usable code(s)" \
			|| bad "by invitation with NO usable code: nobody can sign up — add one in the admin or run manage.py signup --new" ;;
	*Open*) ok "OPEN: anyone can sign up (set in the admin)" ;;
	*) ok "closed: nobody can sign up (set in the admin)" ;;
	esac
else
	bad "could not read the sign-up policy (manage.py signup)"
fi

head_ "8. mail actually goes out"
mailhost=$(grep -oP '^HOPLITA_EMAIL_HOST=\K\S+' "$ENVFILE" 2>/dev/null || true)
if [ -z "$mailhost" ]; then
	bad "no HOPLITA_EMAIL_HOST: the PIN would be printed to the journal and nobody could sign up"
else
	ok "SMTP backend configured ($mailhost)"
	mailport=$(grep -oP '^HOPLITA_EMAIL_PORT=\K\d+' "$ENVFILE" 2>/dev/null || echo 25)
	if timeout 5 bash -c "cat < /dev/null > /dev/tcp/$mailhost/$mailport" 2>/dev/null; then
		ok "something is listening on $mailhost:$mailport (postfix)"
	else
		bad "nothing answers on $mailhost:$mailport — mail will fail and sign-up dies with it"
	fi

	case "$mailhost" in
	127.0.0.1 | localhost | ::1)
		relay=$(postconf -h relayhost 2>/dev/null || true)
		if [ -n "$relay" ]; then
			ok "postfix relays through $relay (it is not delivering by itself)"
		elif [ -z "$DOMAIN" ]; then
			ok "local postfix, no relayhost (pass DOMAIN= to report on direct delivery)"
		else
			ptr=$(python3 - "$DOMAIN" 2>/dev/null <<-'PY'
				import socket, sys
				try:
				    print(socket.gethostbyaddr(socket.gethostbyname(sys.argv[1]))[0])
				except Exception:
				    pass
			PY
			)
			if [ -n "$ptr" ] && [ "${ptr%.}" = "${DOMAIN%.}" ]; then
				ok "local postfix, rDNS is $ptr — direct delivery is plausible"
			else
				ok "local postfix, delivering direct to MX"
				printf '  \033[33mNOTE\033[0m rDNS is %s, not %s, and there is no relayhost.\n' \
					"${ptr:-none}" "$DOMAIN"
				printf '       Mail to strangers on gmail/outlook will very likely be\n'
				printf '       refused or filed as spam. Fine while the accounts are\n'
				printf '       yours; set a relayhost before opening sign-up wider.\n'
			fi
		fi

		stuck=$(postqueue -p 2>/dev/null | grep -cE '^[0-9A-F]{8,}' || true)
		if [ "${stuck:-0}" -eq 0 ]; then
			ok "the mail queue is empty (nothing has failed to leave)"
		elif [ -n "$relay" ]; then
			bad "$stuck message(s) stuck in the queue despite relayhost $relay — the relay is not working: postqueue -p"
		else
			ok "postfix is up; $stuck message(s) queued"
			printf '  \033[33mNOTE\033[0m those %s are not reaching their MX. Expected without a\n' "$stuck"
			printf '       relayhost on this box. Inspect with: postqueue -p\n'
		fi

		if [ -n "${MAIL_PROBE:-}" ]; then
			printf 'Subject: hoplita-fed acceptance probe\r\n\r\nsent by accept.sh\r\n' \
				| sendmail -f "no-reply@${DOMAIN:-localhost}" "$MAIL_PROBE" 2>/dev/null
			sleep 8
			left=$(postqueue -p 2>/dev/null | grep -cE '^[0-9A-F]{8,}' || true)
			[ "${left:-0}" -eq 0 ] \
				&& ok "probe to $MAIL_PROBE left the machine (check that it arrived)" \
				|| bad "the probe to $MAIL_PROBE is still queued — it is not getting out"
		else
			printf '  \033[33mSKIP\033[0m set MAIL_PROBE=you@somewhere to send a real one\n'
		fi
		;;
	*)
		ok "mail goes to an external SMTP host, not to a local postfix"
		;;
	esac
fi

head_ "9. TLS from outside"
if [ -z "$DOMAIN" ]; then
	printf '  \033[33mSKIP\033[0m pass DOMAIN=<your domain> to check the certificate\n'
elif [ "$svc_up" -eq 0 ] && ! systemctl is-active --quiet caddy 2>/dev/null; then
	bad "not testable yet: neither $SERVICE nor caddy is running"
else
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$DOMAIN/")
	[ "$code" = "200" ] && ok "https://$DOMAIN/ -> 200 with a valid certificate" \
		|| bad "https://$DOMAIN/ -> $code (DNS, ports 80/443, or ACME)"
	leak=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$DOMAIN/health")
	[ "$leak" = "404" ] \
		&& ok "/health is not reachable from outside" \
		|| bad "https://$DOMAIN/health -> $leak: it hands the database engine, cache backend and worker PIDs to anyone"
	red=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://$DOMAIN/")
	[ "$red" = "301" ] || [ "$red" = "308" ] \
		&& ok "http redirects to https ($red)" || bad "http does not redirect ($red)"
fi

head_ "10. the real thing: cross-process WebSockets and a shared throttle"
if [ ! -x "$PREFIX/venv/bin/python" ]; then
	bad "no venv at $PREFIX/venv — nothing to smoke yet"
elif [ "$svc_up" -eq 0 ]; then
	bad "$SERVICE is not running — fed_smoke has nothing to talk to"
elif as_hoplita "$PREFIX/venv/bin/python" "$PREFIX/manage.py" \
	fed_smoke --url "$LOCAL_URL" 2>&1 | tee /tmp/fed_smoke.$$ | tail -3; then
	ok "fed_smoke passed"
else
	bad "fed_smoke FAILED — see /tmp/fed_smoke.$$"
fi

head_ "11. nothing is listening on a public interface that should not be"
PUBLIC_OK=${PUBLIC_OK:-22 80 443}
pub=""
while read -r addr; do
	[ -n "$addr" ] || continue
	port=${addr##*:}
	host=${addr%:*}
	case "$host" in
	127.* | "::1" | "[::1]" | "localhost") continue ;;   # loopback: fine
	esac
	case " $PUBLIC_OK " in *" $port "*) continue ;; esac
	pub="$pub $port"
done <<-EOF
	$(ss -lntH 2>/dev/null | awk '{print $4}')
EOF
pub=$(printf '%s\n' $pub | sort -un | tr '\n' ' ')
if [ -z "${pub// /}" ]; then
	ok "only $PUBLIC_OK are exposed"
else
	bad "also listening on the outside:$pub — identify each with 'ss -lntp' and close what you did not put there (9090 is usually cockpit.socket)"
fi
for p in 5432 6379 8600 25 2019; do
	if ss -lntH 2>/dev/null | awk '{print $4}' \
		| grep -qE "^(0\.0\.0\.0|\[::\]|\*):$p$"; then
		bad "port $p is bound to ALL interfaces — that is the database, the cache, the app or Caddy's admin API, straight onto the internet"
	fi
done

printf '\n'
if [ "$fails" -eq 0 ]; then
	printf '\033[32;1mACCEPTED\033[0m — the deployment holds.\n'
	exit 0
fi
printf '\033[31;1m%d check(s) failed\033[0m — not deployed.\n' "$fails"
exit 1
