#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$APP_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$APP_DIR/.venv/bin/python"
else
    PYTHON_BIN=python3
fi
exec "$PYTHON_BIN" "$APP_DIR/app.py" "$@"
