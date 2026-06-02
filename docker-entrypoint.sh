#!/bin/sh
set -e

mkdir -p "${UPLOAD_DIR:-/app/uploads}"

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting RezDrop..."
exec uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8080}"
