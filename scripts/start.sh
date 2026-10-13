#!/bin/sh
# Production-style local start: API + built frontend on one origin, bound to loopback only.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MSLV_DATA_ROOT="${MSLV_DATA_ROOT:-$ROOT/data}"
export MSLV_FRONTEND_DIST="${MSLV_FRONTEND_DIST:-$ROOT/frontend/dist}"
cd "$ROOT/backend" || exit 1
exec .venv/bin/python -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port "${PORT:-8000}"
