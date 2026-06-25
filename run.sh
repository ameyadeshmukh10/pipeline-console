#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Prefer the project venv python (-m uvicorn works even without the console script).
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8787}" --reload
