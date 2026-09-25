#!/usr/bin/env bash
# Short start: ./run.sh  (optional PORT, e.g. PORT=9000 ./run.sh)
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
