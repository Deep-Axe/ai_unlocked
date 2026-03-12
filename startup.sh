#!/bin/bash
set -e

# Find gunicorn inside the antenv virtual environment built by Oryx
GUNICORN=$(find /tmp -maxdepth 4 -name "gunicorn" -path "*/antenv/bin/*" 2>/dev/null | head -1)

if [ -z "$GUNICORN" ]; then
    echo "ERROR: gunicorn not found in antenv, falling back to PATH"
    GUNICORN=gunicorn
fi

echo "Using gunicorn at: $GUNICORN"

exec "$GUNICORN" backend.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 1 \
    --bind 0.0.0.0:8000 \
    --timeout 120
