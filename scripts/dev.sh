#!/bin/sh
# Development: API with auto-reload on :8000 and Vite on :5173 (proxying /api). Ctrl+C stops both.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -x "$ROOT/.tools/bin/node" ] && PATH="$ROOT/.tools/bin:$PATH"
export MSLV_DATA_ROOT="${MSLV_DATA_ROOT:-$ROOT/data}"
( cd "$ROOT/backend" && exec .venv/bin/python -m uvicorn app.main:app_factory --factory --reload --host 127.0.0.1 --port 8000 ) &
API=$!
trap 'kill $API 2>/dev/null' EXIT INT TERM
cd "$ROOT/frontend" && npm run dev
