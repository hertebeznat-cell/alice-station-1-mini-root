#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
PYTHON="${STATION_ROOT_PYTHON:-$HOME/.local/share/station-mini-root/venv/bin/python}"
exec "$PYTHON" station_mini_root.py
