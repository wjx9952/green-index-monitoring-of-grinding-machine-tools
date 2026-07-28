#!/bin/sh
APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$APP_DIR/.tools${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m raspberry_pi.desktop

