#!/usr/bin/env bash
set -euo pipefail
# Invoked as root only inside the distro chosen in the application.
export DEBIAN_FRONTEND=noninteractive
test "$(id -u)" = 0
apt-get update
apt-get install -y --no-install-recommends ca-certificates python3-venv mtd-utils fakeroot
python3 -m venv /opt/station-mini-root/venv
/opt/station-mini-root/venv/bin/python -m pip install 'ubi-reader==0.8.14'
/opt/station-mini-root/venv/bin/python -c 'import ubireader'
command -v mkfs.ubifs
command -v ubinize
printf 'Station Mini Root builder ready\n'
