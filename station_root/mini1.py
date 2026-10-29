"""What Station Mini Root knows about Station Mini 1 (YNDX-0004).

Hardware facts and firmware layout live here, in one place, instead of being
spread through the workflow. The list of verified firmware binaries is data:
station_root/data/known-firmware.json.
"""

from __future__ import annotations

import json
from pathlib import Path

MODEL = "YNDX-0004"

# USB boot mode (Amlogic "WorldCup") of the speaker.
USB_VENDOR_ID = 0x1B8E
USB_PRODUCT_ID = 0xC003

# The only partition this tool reads or writes.
SYSTEM_SIZE = 80 * 1024 * 1024
# SHA-256 of the factory system partition; a backup with this hash needs no metadata.
STOCK_SYSTEM_SHA256 = "ce21c54bd9fdde0ab1489429a503d07fb4c8be3c8fc78a5b18e48dd283d32f6f"

# NAND/UBI geometry of the system partition.
UBI_PEB_SIZE = 131072
UBI_MIN_IO = 2048
UBI_LEB_SIZE = 126976

# The Alice ("quasar") runtime inside the rootfs.
QUASAR = "system/vendor/quasar"
MAIND = "/system/vendor/quasar/maind"
UPDATER_SWITCHER = "/system/vendor/quasar/updater_switcher"
SWITCHER_ARGS = ["/data/quasar/use_updater", MAIND, "/system/vendor/quasar/updater"]
# Launcher task names and binaries that can install OTA updates.
UPDATER_NAMES = frozenset({"updatesd", "updater", "updater_switcher", "a01localupdate", "device_ota_update.sh"})
OTA_ENTRYPOINTS = (f"{QUASAR}/device_ota_update.sh", f"{QUASAR}/a01localupdate")

# Marker written into the patched rootfs; the app reads it back over ADB.
ROOT_MARKER = "etc/station-mini-root.json"


def _known_firmware():
    path = Path(__file__).with_name("data") / "known-firmware.json"
    return json.loads(path.read_text(encoding="utf-8"))


_KNOWN = _known_firmware()
KNOWN_UPDATER_SWITCHER_HASHES = frozenset(entry["sha256"] for entry in _KNOWN["updater_switcher"])
KNOWN_NOUPDATE_SCRIPT = _KNOWN["noupdate_script"]["text"].encode()


def is_main_task(task, argument):
    """The Alice main process, started with one mode flag."""
    return task.get("name") == "maind" and task.get("path") == MAIND and task.get("arg") == [argument]


def is_updater_task(task):
    path = task.get("path", "")
    return task.get("name") in {"updater", "updater_switcher"} or (
        isinstance(path, str) and Path(path).name in {"updater", "updater_switcher"}
    )
