#!/usr/bin/env bash
set -euo pipefail
# Debian / Ubuntu / WSL. Installs tools only on the computer, never on a station.
sudo apt-get update
sudo apt-get install -y python3-venv python3-tk mtd-utils fakeroot libusb-1.0-0 adb
python3 -m venv "$HOME/.local/share/station-mini-root/venv"
"$HOME/.local/share/station-mini-root/venv/bin/python" -m pip install -r "$(dirname -- "$0")/../requirements-linux.txt"
echo 'Linux-инструменты установлены.'
