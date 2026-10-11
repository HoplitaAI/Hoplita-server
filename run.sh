#!/bin/bash
set -e
cd "$(dirname "$0")"
PY=${PY:-python}

if [ "$1" = "--prod-local" ]; then
    : "${WEB_CONCURRENCY:=4}"
    : "${HOPLITA_SECRET_KEY:=$($PY -c 'import secrets;print(secrets.token_urlsafe(64))')}"
    : "${HOPLITA_ALLOWED_HOSTS:=localhost,127.0.0.1}"
    export WEB_CONCURRENCY HOPLITA_SECRET_KEY HOPLITA_ALLOWED_HOSTS
    [ -n "$HOPLITA_DB_NAME" ] || {
        echo "x --prod-local needs HOPLITA_DB_NAME (+_USER/_PASSWORD):"
        echo "  multi-worker rides the Postgres bus, and SQLite cannot."
        exit 1
    }
    echo "[prod-local] $WEB_CONCURRENCY workers - Postgres bus on $HOPLITA_DB_NAME"
    $PY manage.py migrate --noinput
    $PY manage.py createcachetable
    export MALLOC_ARENA_MAX=2 MALLOC_MMAP_THRESHOLD_=131072 MALLOC_TRIM_THRESHOLD_=131072
    exec $PY -m gunicorn hoplitafed.asgi:application \
        --worker-class uvicorn.workers.UvicornWorker \
        --bind 0.0.0.0:8600 --timeout 120 --graceful-timeout 30 \
        --max-requests 2000 --max-requests-jitter 200 \
        --access-logfile - --error-logfile -
fi

export HOPLITA_DEBUG=${HOPLITA_DEBUG:-1}
export HOPLITA_ALLOWED_HOSTS=${HOPLITA_ALLOWED_HOSTS:-localhost,127.0.0.1}
$PY manage.py migrate --noinput
echo "[dev] daphne - 1 process - DEBUG=$HOPLITA_DEBUG"
exec $PY -m daphne -b 0.0.0.0 -p 8600 hoplitafed.asgi:application
