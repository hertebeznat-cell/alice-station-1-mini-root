#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
test "$(id -u)" = 0
apt-get update
apt-get install -y --no-install-recommends ca-certificates python3-venv mtd-utils fakeroot adb libusb-1.0-0
python3 -m venv /opt/station-mini-root/venv
/opt/station-mini-root/venv/bin/python -m pip install 'ubi-reader==0.8.14'
mkdir -p /etc/udev/rules.d
cat > /etc/udev/rules.d/70-station-mini-root.rules <<'RULES'
SUBSYSTEM=="usb", ATTR{idVendor}=="1b8e", ATTR{idProduct}=="c003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ATTR{idProduct}=="4e26", MODE="0660", GROUP="plugdev", TAG+="uaccess"
RULES
if command -v udevadm >/dev/null; then
    udevadm control --reload-rules
fi
