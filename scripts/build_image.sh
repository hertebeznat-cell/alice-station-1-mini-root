#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
PYTHON="${STATION_ROOT_PYTHON:-$HOME/.local/share/station-mini-root/venv/bin/python}"
if [[ ! -x "$PYTHON" ]] || ! command -v fakeroot >/dev/null; then
    echo 'Сначала выполните: bash scripts/setup-linux.sh' >&2
    exit 1
fi
exec fakeroot -- "$PYTHON" -m station_root.image_builder "$@"
