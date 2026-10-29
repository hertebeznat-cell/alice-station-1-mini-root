"""Offline integration check: compare every rootfs entry against the stock image.

Usage: python tests/verify_image.py STOCK IMAGE PUBLIC_KEY
Requires ubi-reader. No USB access, no extraction to the host filesystem.
"""

import hashlib
import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from station_root.image_builder import validate_public_key
from station_root.workflow import validate_image
from ubireader.ubi_io import ubi_file, leb_virtual_file
from ubireader.ubi import ubi
from ubireader.ubifs import ubifs, walk
from ubireader.ubifs.output import process_reg_file


def inventory(path):
    handle = ubi_file(str(path), 131072)
    container = ubi(handle)
    assert len(container.images) == 1
    image = container.images[0]
    volume = image.volumes["rootfs"]
    assert volume.vol_id == 0
    root = ubifs(leb_virtual_file(container, volume.get_blocks(container.blocks)))
    inodes = {}
    bad = []
    walk.index(root, root.master_node.root_lnum, root.master_node.root_offs, inodes, bad)
    assert not bad, bad
    entries = {}

    def visit(number, prefix):
        inode = inodes[number]
        for dent in inode.get("dent", []):
            path = prefix + "/" + dent.name
            current = inodes[dent.inum]
            ino = current["ino"]
            metadata = (ino.mode, ino.uid, ino.gid, ino.nlink)
            if stat.S_ISREG(ino.mode):
                data = process_reg_file(root, current, path, inodes)
            elif stat.S_ISLNK(ino.mode):
                data = ino.data
            else:
                data = ino.data if not stat.S_ISDIR(ino.mode) else b""
            entries[path] = (metadata, hashlib.sha256(data).hexdigest(), data)
            if stat.S_ISDIR(ino.mode):
                visit(dent.inum, path)

    visit(1, "")
    handle.close()
    return entries


def main():
    stock, patched, key = map(Path, sys.argv[1:])
    report = validate_image(patched, key)
    before, after = inventory(stock), inventory(patched)
    changed = {
        "/usr/bin/adbd",
        "/adb_keys",
        "/etc/init.d/S89usbgadget",
        "/system/vendor/quasar/activate_adb.sh",
        "/etc/station-mini-root.json",
        "/system/vendor/quasar/launch.json",
        "/system/vendor/quasar/device_ota_update.sh",
        "/system/vendor/quasar/a01localupdate",
    }
    assert set(after) - set(before) <= changed
    assert not (set(before) - set(after)), "Deleted stock entries"
    for name in before.keys() - changed:
        assert before[name][:2] == after[name][:2], "Unexpected data/metadata change: " + name
    original_adbd = before.get("/usr/bin/adbd_backup", before.get("/usr/bin/adbd"))
    assert original_adbd is not None, "No original adbd in source image"
    assert after["/usr/bin/adbd"][1] == original_adbd[1]
    assert after["/usr/bin/adbd"][0][0] & 0o777 == 0o755
    assert after["/adb_keys"][2].decode() == validate_public_key(key)
    assert b"if false; then" in after["/etc/init.d/S89usbgadget"][2]
    assert b"killall adbd" not in after["/system/vendor/quasar/activate_adb.sh"][2]
    assert json.loads(after["/etc/station-mini-root.json"][2])["ota_disabled"] is True
    launch = json.loads(after["/system/vendor/quasar/launch.json"][2])
    assert "--updatesd" not in json.dumps(launch)
    for entry in ("/system/vendor/quasar/device_ota_update.sh", "/system/vendor/quasar/a01localupdate"):
        if entry in after:
            assert b"exit 78" in after[entry][2]
    print(
        json.dumps(
            {
                "result": "PASS",
                "stock_entries": len(before),
                "patched_entries": len(after),
                "unchanged_entries_checked": len(before.keys() - changed),
                "image_sha256": report["image_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
