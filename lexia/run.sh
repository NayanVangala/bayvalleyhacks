#!/usr/bin/env bash
set -e

# Resolve the repo root and cd to it.
# This script lives in lexia/, so the repo root is one level up.
# The fallback branch also handles being placed under lexia/scripts/.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SCRIPT_DIR/../lexia/.venv/bin/python" ]; then
  cd "$SCRIPT_DIR/.."
elif [ -x "$SCRIPT_DIR/../../lexia/.venv/bin/python" ]; then
  cd "$SCRIPT_DIR/../.."
else
  cd "$SCRIPT_DIR/.."
fi

exec lexia/.venv/bin/python -m uvicorn lexia.api.app:app --host 0.0.0.0 --port 8100
