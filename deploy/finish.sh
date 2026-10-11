#!/usr/bin/env bash

set -euo pipefail

PREFIX=${PREFIX:-/opt/hoplita-fed}
ENVFILE=${ENVFILE:-/etc/hoplita-fed.env}
TGZ=${TGZ:-/tmp/hoplita-fed.tgz}
DOMAIN=${DOMAIN:-}
PGDATA=${PGDATA:-/var/lib/pgsql/data}
DBNAME=${DBNAME:-hoplitafed}
DBUSER=${DBUSER:-hoplita}

MAIL_HOST=${MAIL_HOST:-127.0.0.1}
MAIL_PORT=${MAIL_PORT:-25}
MAIL_TLS=${MAIL_TLS:-0}
MAIL_USER=${MAIL_USER:-}
MAIL_PASS=${MAIL_PASS:-}
MAIL_FROM=${MAIL_FROM:-no-reply@$DOMAIN}

INVITE=$(sed -n 's/^HOPLITA_INVITE_CODES=//p' "$ENVFILE" 2>/dev/null | head -1)
[ -n "$INVITE" ] || INVITE=$(python3 -c 'import secrets;print(secrets.token_urlsafe(12))')

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
[ -n "$DOMAIN" ] || { echo "set DOMAIN= to the name this server answers on"; exit 1; }
[ -r "$TGZ" ] || { echo "no tarball at $TGZ (set TGZ=)"; exit 1; }

step "packages"
rpm -q epel-release >/dev/null 2>&1 || dnf -y install epel-release
dnf -y config-manager --set-enabled crb 2>/dev/null || crb enable || true
dnf -y install python3 python3-devel gcc postgresql-server postfix caddy

step "user + tree at $PREFIX"
id "$DBUSER" >/dev/null 2>&1 \
	|| useradd --system --home-dir "$PREFIX" --shell /sbin/nologin "$DBUSER"
mkdir -p "$PREFIX"
tar xzf "$TGZ" -C "$PREFIX" --strip-components=1
chmod +x "$PREFIX/deploy/accept.sh"

step "venv"
[ -x "$PREFIX/venv/bin/python" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet -r "$PREFIX/requirements.txt"
chown -R "$DBUSER:$DBUSER" "$PREFIX"

step "postgres"
[ -f "$PGDATA/PG_VERSION" ] || postgresql-setup --initdb
systemctl enable --now postgresql
sed -i -E \
	-e 's|^(host[[:space:]]+all[[:space:]]+all[[:space:]]+127\.0\.0\.1/32[[:space:]]+).*|\1scram-sha-256|' \
	-e 's|^(host[[:space:]]+all[[:space:]]+all[[:space:]]+::1/128[[:space:]]+).*|\1scram-sha-256|' \
	"$PGDATA/pg_hba.conf"
systemctl reload postgresql

for _ in $(seq 1 30); do
	sudo -u postgres psql -tAc 'SELECT 1' >/dev/null 2>&1 && break
	sleep 1
done

if [ -f "$ENVFILE" ] && grep -q '^HOPLITA_DB_PASSWORD=.' "$ENVFILE"; then
	DBPASS=$(sed -n 's/^HOPLITA_DB_PASSWORD=//p' "$ENVFILE" | head -1)
else
	DBPASS=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
fi
psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 "$@"; }
psql_su -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DBUSER'" | grep -q 1 \
	|| psql_su -c "CREATE ROLE $DBUSER LOGIN"
psql_su -c "ALTER ROLE $DBUSER WITH LOGIN PASSWORD '$DBPASS'"
psql_su -tAc "SELECT 1 FROM pg_database WHERE datname='$DBNAME'" | grep -q 1 \
	|| sudo -u postgres createdb -O "$DBUSER" "$DBNAME"

step "postfix"
systemctl enable --now postfix
case "$MAIL_HOST" in
127.0.0.1 | localhost | ::1)
	printf '  local postfix on 25. It has no relayhost, so it delivers direct to\n'
	printf '  MX: fine for your own accounts, watch `postqueue -p` before opening\n'
	printf '  sign-up wider. Set MAIL_HOST/MAIL_USER/MAIL_PASS to relay instead.\n' ;;
esac

step "$ENVFILE"
if [ -f "$ENVFILE" ] && grep -q '^HOPLITA_SECRET_KEY=.' "$ENVFILE"; then
	SECRET=$(sed -n 's/^HOPLITA_SECRET_KEY=//p' "$ENVFILE" | head -1)
else
	SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(64))')
fi
umask 077
cat >"$ENVFILE" <<EOF
HOPLITA_SECRET_KEY=$SECRET
HOPLITA_DEBUG=0
HOPLITA_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN,127.0.0.1,localhost
HOPLITA_CSRF_ORIGINS=https://$DOMAIN,https://www.$DOMAIN
HOPLITA_BEHIND_PROXY=1
HOPLITA_HSTS_SECONDS=0
WEB_CONCURRENCY=4
HOPLITA_CHANNEL_CAPACITY=1500
HOPLITA_DB_NAME=$DBNAME
HOPLITA_DB_USER=$DBUSER
HOPLITA_DB_PASSWORD=$DBPASS
HOPLITA_DB_HOST=127.0.0.1
HOPLITA_DB_PORT=5432
HOPLITA_DB_CONN_MAX_AGE=0
HOPLITA_EMAIL_HOST=$MAIL_HOST
HOPLITA_EMAIL_PORT=$MAIL_PORT
HOPLITA_EMAIL_USE_TLS=$MAIL_TLS
HOPLITA_EMAIL_USER=$MAIL_USER
HOPLITA_EMAIL_PASSWORD=$MAIL_PASS
HOPLITA_EMAIL_FROM=$MAIL_FROM
HOPLITA_INVITE_CODES=$INVITE
EOF
chmod 600 "$ENVFILE"
umask 022

step "hoplita-fed.service"
sed "s|/opt/hoplita-fed|$PREFIX|g" "$PREFIX/deploy/hoplita-fed.service" >/etc/systemd/system/hoplita-fed.service
sed -i 's|^After=.*|After=network-online.target postgresql.service|' \
	/etc/systemd/system/hoplita-fed.service
systemctl daemon-reload
systemctl enable hoplita-fed
systemctl restart hoplita-fed

step "caddy"
sed "s/example\.com/$DOMAIN/g" "$PREFIX/deploy/Caddyfile" >/etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl restart caddy

if systemctl is-active --quiet firewalld; then
	firewall-cmd -q --permanent --add-service=http --add-service=https
	firewall-cmd -q --reload
fi

step "acceptance gate"
sleep 5   # gunicorn runs migrations in ExecStartPre before it binds
PREFIX="$PREFIX" ENVFILE="$ENVFILE" DOMAIN="$DOMAIN" "$PREFIX/deploy/accept.sh"
rc=$?

printf '\n\033[1mINVITATION CODE: %s\033[0m\n' "$INVITE"
printf 'Registration is invite-only. This is the code, and without it nobody\n'
printf 'can create an account on this server — you included. Rotate it by\n'
printf 'editing HOPLITA_INVITE_CODES in %s (comma-separated) and\n' "$ENVFILE"
printf 'restarting hoplita-fed.\n'
exit $rc
