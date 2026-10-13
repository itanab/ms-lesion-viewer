#!/bin/sh
# One-time setup: backend venv (uv), frontend build, e2e tooling. Uses a project-local Node if none is installed.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
( cd backend && uv sync --frozen )

if ! command -v node >/dev/null && [ ! -x "$ROOT/.tools/bin/node" ]; then
  echo "Node.js not found: installing a project-local copy under .tools/ (nothing system-wide)"
  uv venv --python 3.11 .tools/venv >/dev/null
  uv pip install -q -p .tools/venv/bin/python nodejs-wheel-binaries
  W="$(ls -d "$ROOT"/.tools/venv/lib/python*/site-packages/nodejs_wheel)"
  mkdir -p .tools/bin
  printf '#!/bin/sh\nexec "%s/bin/node" "$@"\n' "$W" > .tools/bin/node
  printf '#!/bin/sh\nexport PATH="%s/.tools/bin:$PATH"\nexec "%s/bin/node" "%s/lib/node_modules/npm/bin/npm-cli.js" "$@"\n' "$ROOT" "$W" "$W" > .tools/bin/npm
  printf '#!/bin/sh\nexport PATH="%s/.tools/bin:$PATH"\nexec "%s/bin/node" "%s/lib/node_modules/npm/bin/npx-cli.js" "$@"\n' "$ROOT" "$W" "$W" > .tools/bin/npx
  chmod +x .tools/bin/*
fi
[ -x "$ROOT/.tools/bin/node" ] && PATH="$ROOT/.tools/bin:$PATH"

( cd frontend && npm ci --no-audit --no-fund && npm run build )
echo "Done. Start the app with: scripts/start.sh   then open http://127.0.0.1:8000"
