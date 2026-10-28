"""Linux-only offline builder. Runs under fakeroot to retain file metadata."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from .usb_transport import SYSTEM_SIZE
from . import __version__


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_public_key(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    try:
        decoded = base64.b64decode(text.split()[0], validate=True)
    except (ValueError, IndexError) as exc:
        raise ValueError("Нужен публичный ADB-ключ .pub, не приватный ключ") from exc
    if len(decoded) != 524 or int.from_bytes(decoded[:4], "little") != 64:
        raise ValueError("Неверный формат публичного RSA-2048 ADB-ключа")
    return text + "\n"


def inspect_layout(source):
    from ubireader.ubi_io import ubi_file, leb_virtual_file
    from ubireader.ubi import ubi
    from ubireader.ubifs import ubifs
    handle = ubi_file(str(source), 131072)
    try:
        container = ubi(handle)
        if container.min_io_size != 2048 or container.leb_size != 126976 or len(container.images) != 1:
            raise ValueError("Unsupported UBI geometry / multiple images")
        image = container.images[0]
        if set(image.volumes) != {"rootfs"}:
            raise ValueError("Expected one rootfs volume")
        volume = image.volumes["rootfs"]
        if volume.vol_id != 0 or volume.vol_rec.vol_type != 1:
            raise ValueError("Expected dynamic rootfs volume 0")
        count = volume.vol_rec.reserved_pebs
        filesystem = ubifs(leb_virtual_file(container, volume.get_blocks(container.blocks)))
        superblock = filesystem.superblock_node
        if not 64 < count <= 620 or superblock.fmt_version != 4 or superblock.key_hash != 0:
            raise ValueError("Unsupported UBIFS layout")
        return {"volume_bytes": count * 126976, "max_lebs": superblock.max_leb_cnt,
                "image_seq": image.image_seq}
    finally:
        handle.close()


KNOWN_NOUPDATE_SCRIPT = (b'#!/bin/sh\n# disabled by noupdate\n'
                        b'logger -t noupdate "OTA apply blocked" 2>/dev/null\nexit 0\n')

# Confirmed Mini 1 switcher: selects legacy --updatesd or the dedicated updater
# and its --updater_gateway. Other binaries with this filename are not trusted.
KNOWN_UPDATER_SWITCHER_SHA256 = "0cc051e648caff435b4bef9d1b310825093ab7ee04ec2db61672f9b7c1fe63cb"
# Mini 1 1.305.4.16.3237824040.20250721: byte-for-byte identical to the
# verified switcher except its 20-byte GNU Build ID. Keep exact file hashes;
# arbitrary build-ID differences or other switcher binaries are not accepted.
KNOWN_UPDATER_SWITCHER_HASHES = frozenset({
    KNOWN_UPDATER_SWITCHER_SHA256,
    "2e191284fb74678a3c62c7dc935fe7c4ceab24c8d7f07c24b82ccad379923930",
})


def launcher_tasks(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "tasks":
                if not isinstance(child, list) or not all(isinstance(task, dict) for task in child):
                    raise RuntimeError("Unsupported tasks in launch.json; OTA policy is unknown")
                yield from child
            yield from launcher_tasks(child)
    elif isinstance(value, list):
        for child in value:
            yield from launcher_tasks(child)


def known_updater_switcher(root, task, tasks):
    if (task.get("name") != "updater_switcher" or
            task.get("path") != "/system/vendor/quasar/updater_switcher" or
            task.get("arg") != ["/data/quasar/use_updater", "/system/vendor/quasar/maind",
                                "/system/vendor/quasar/updater"]):
        return False
    if not any(t.get("name") == "maind" and t.get("path") == "/system/vendor/quasar/maind"
               and t.get("arg") == ["--platformd"] for t in tasks):
        return False
    binary = Path(root) / "system/vendor/quasar/updater_switcher"
    return (not binary.is_symlink() and binary.is_file() and
            sha256(binary) in KNOWN_UPDATER_SWITCHER_HASHES)


def firmware_diagnostics(root, source_hash):
    """Export only version/check results and hashes, never account configs or keys."""
    root = Path(root)
    vendor = root / "system/vendor/quasar"
    report = {"format": 1, "builder_version": __version__, "source_sha256": source_hash,
              "stage": "rootfs-patch-failed", "firmware_written": False}
    for name in ("version_full", "launch.json", "updater_switcher"):
        path = vendor / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            report[name] = {"present_regular_file": False}
            continue
        report[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        if name == "version_full" and path.stat().st_size <= 256:
            value = path.read_text(encoding="utf-8", errors="replace").strip()
            if re.fullmatch(r"[A-Za-z0-9._-]{1,150}", value):
                report[name]["version"] = value
    launch = vendor / "launch.json"
    if report["launch.json"].get("present_regular_file") is False:
        return report
    try:
        tasks = list(launcher_tasks(json.loads(launch.read_text(encoding="utf-8"))))
        report["platformd_main_matches"] = any(
            t.get("name") == "maind" and t.get("path") == "/system/vendor/quasar/maind"
            and t.get("arg") == ["--platformd"] for t in tasks)
        report["switcher_checks"] = [{
            "name_matches": t.get("name") == "updater_switcher",
            "path_matches": t.get("path") == "/system/vendor/quasar/updater_switcher",
            "arguments_match": t.get("arg") == ["/data/quasar/use_updater",
                "/system/vendor/quasar/maind", "/system/vendor/quasar/updater"],
        } for t in tasks if t.get("name") in {"updater", "updater_switcher"} or
            isinstance(t.get("path"), str) and Path(t["path"]).name in {"updater", "updater_switcher"}]
        report["switcher_hash_matches"] = (report["updater_switcher"].get("sha256") in
                                             KNOWN_UPDATER_SWITCHER_HASHES)
    except (ValueError, RuntimeError):
        report["launch_parse_failed"] = True
    return report


def known_noupdate_policy(root, tasks):
    """Recognize the previously installed noupdate patch, not arbitrary absence.

    Its exact inert apply script and the normal Mini 1 main task must both be
    present. Unknown updater launch paths are not silently accepted.
    """
    script = root / "system/vendor/quasar/device_ota_update.sh"
    if script.is_symlink() or not script.is_file():
        return False
    if script.read_bytes().replace(b"\r\n", b"\n") != KNOWN_NOUPDATE_SCRIPT:
        return False
    if not tasks or not all(isinstance(task, dict) for task in tasks):
        return False
    main_found = False
    updater_names = {"updatesd", "updater", "updater_switcher", "a01localupdate", "device_ota_update.sh"}
    for task in tasks:
        arguments = task.get("arg", [])
        if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
            return False
        path = task.get("path", "")
        if not isinstance(path, str):
            return False
        if task.get("name") in updater_names or Path(path).name in updater_names:
            return False
        if any(arg == "--updatesd" or arg.startswith("--updatesd=") or
               Path(arg).name in updater_names for arg in arguments):
            return False
        if (task.get("name") == "maind" and path == "/system/vendor/quasar/maind"
                and arguments == ["--yandexmini"]):
            main_found = True
    return main_found


def disable_ota(root):
    root = Path(root)
    launch = root / "system/vendor/quasar/launch.json"
    data = json.loads(launch.read_text(encoding="utf-8"))
    tasks = list(launcher_tasks(data))
    switchers = set()
    for task in tasks:
        path = task.get("path", "")
        if (task.get("name") in {"updater", "updater_switcher"} or
                isinstance(path, str) and Path(path).name in {"updater", "updater_switcher"}):
            if not known_updater_switcher(root, task, tasks):
                raise RuntimeError("Unknown updater task or switcher binary; OTA blocking is not confirmed")
            switchers.add(id(task))
    removed, retained = [], []
    def visit(value):
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key == "tasks" and isinstance(child, list):
                    kept = []
                    for task in child:
                        if isinstance(task, dict) and (id(task) in switchers or task.get("name") == "updatesd" or
                                "--updatesd" in task.get("arg", [])):
                            removed.append(task.get("name", "updatesd"))
                        else:
                            kept.append(task)
                            retained.append(task)
                    value[key] = kept
                visit(value[key])
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    previous_policy = not removed and known_noupdate_policy(root, retained)
    if not removed and not previous_policy:
        raise RuntimeError("Cannot identify updatesd in launch.json; refusing to claim OTA is blocked")
    entrypoints = [root / rel for rel in ("system/vendor/quasar/device_ota_update.sh",
                                         "system/vendor/quasar/a01localupdate")]
    if not any(path.is_file() or path.is_symlink() for path in entrypoints):
        raise RuntimeError("No known OTA entry point was found")
    launch.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    blocked = []
    for path in entrypoints:
        if path.is_file() or path.is_symlink():
            # Replace the entry, never follow a symlink into a shared multicall binary.
            if path.is_symlink():
                path.unlink()
            path.write_text('#!/bin/sh\necho "OTA disabled by Station Mini Root" >&2\nexit 78\n', encoding="utf-8")
            path.chmod(0o755)
            blocked.append("/" + path.relative_to(root).as_posix())
    return {"removed_tasks": removed, "blocked_entrypoints": blocked,
            "source_policy": ("noupdate-v1" if previous_policy else
                              "stock-updater-switcher-v1" if switchers else "stock-updatesd")}


def patch_adb_activation(content):
    if "Keep persistent USB ADB" in content:
        raise RuntimeError("This source is already patched. Restore a pre-root backup before reinstalling.")
    old = re.compile(r'(?m)^[ \t]*killall adbd\s*\n[ \t]*mv "\$\{ADBD\}" "\$\{ADBD_BACKUP\}"\s*\n[ \t]*sync\s*\n[ \t]*echo[^\n]*Stop adb[^\n]*')
    if len(old.findall(content)) == 1:
        return old.sub('    echo "`date`: Keep persistent USB ADB" >> "${ADB_LOG}"', content), "stock-deactivation"
    # The owner's earlier method already replaced the final deactivation
    # branch with this exact log-only branch. Recognize that branch explicitly;
    # do not treat every script without `killall adbd` as compatible.
    legacy = re.compile(r'(?m)^else[ \t]*\n[ \t]*echo "`date`: Ignore adb deactivation \(persistent USB ADB\)" >> "\$\{ADB_LOG\}"[ \t]*\nfi[ \t]*\n?\Z')
    if (not old.findall(content) and len(legacy.findall(content)) == 1 and
            content.count('if [ "$ARG_CMD" = "activate" ]; then') == 1 and
            not re.search(r'\bkillall[ \t]+adbd\b|\bmv[ \t]+"\$\{ADBD\}"', content)):
        return legacy.sub('else\n    echo "`date`: Keep persistent USB ADB" >> "${ADB_LOG}"\nfi\n', content), "legacy-persistent"
    raise RuntimeError("Неизвестный activate_adb.sh; патч остановлен")


def patch_rootfs(root, key_text, block_updates=True):
    root = Path(root)
    daemon = root / "usr/bin/adbd"
    backup = root / "usr/bin/adbd_backup"
    original = backup if backup.is_file() else daemon
    if not original.is_file():
        raise RuntimeError("В исходной прошивке отсутствует adbd/adbd_backup")
    header = original.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or struct.unpack("<H", header[18:20])[0] not in (40, 183):
        raise RuntimeError("adbd is not an ARM / AArch64 ELF executable")
    if original != daemon:
        shutil.copy2(original, daemon)
    daemon.chmod(0o755)
    key = root / "adb_keys"
    key.write_text(key_text, encoding="utf-8", newline="\n")
    key.chmod(0o644)
    script = root / "system/vendor/quasar/activate_adb.sh"
    content = script.read_text(encoding="utf-8")
    if (root / "etc/station-mini-root.json").exists() or "Keep persistent USB ADB" in content:
        raise RuntimeError("This source is already patched. Restore a pre-root backup before reinstalling.")
    content, activation_source = patch_adb_activation(content)
    script.write_text(content, encoding="utf-8", newline="\n")
    script.chmod(0o755)
    gadget = root / "etc/init.d/S89usbgadget"
    content = gadget.read_text(encoding="utf-8")
    old = 'if [ -f "/data/quasar_activate_network_adb" ]; then'
    if content.count(old) != 1:
        raise RuntimeError("Неизвестный S89usbgadget; патч остановлен")
    gadget.write_text(content.replace(old, 'if false; then # Station Mini Root: USB mode'),
                      encoding="utf-8", newline="\n")
    gadget.chmod(0o755)
    # Make absence of essential symlinks/modes a build error, not a bricked device.
    for rel in ("bin/busybox", "sbin/init", "etc/init.d/rcS"):
        p = root / rel
        if not p.is_symlink() and (not p.is_file() or not p.stat().st_mode & 0o111):
            raise RuntimeError(f"Потеряны права/файл: {rel}")
    if not (root / "etc/init.d/S99onboot.sh").is_symlink():
        raise RuntimeError("Потеряна символическая ссылка S99onboot.sh")
    ota = disable_ota(root) if block_updates else {}
    marker = {"tool": "StationMiniRoot", "version": __version__, "ota_disabled": bool(block_updates),
              "ota_policy": ota, "source_adb_activation": activation_source}
    (root / "etc/station-mini-root.json").write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    (root / "etc/station-mini-root.json").chmod(0o644)
    return marker


def build(source, public_key, destination, block_updates=True):
    if sys.platform != "linux":
        raise RuntimeError("Образ собирается в Linux / WSL")
    if os.geteuid() != 0:
        raise RuntimeError("Запустите сборщик через fakeroot: см. scripts/build_image.sh")
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.with_suffix(".json").exists():
        raise FileExistsError("Результат уже существует; выберите новое имя")
    if source.stat().st_size != SYSTEM_SIZE:
        raise ValueError("Expected a complete 80 MiB system backup")
    source_hash = sha256(source)
    layout = inspect_layout(source)
    key_text = validate_public_key(public_key)
    for tool in ("mkfs.ubifs", "ubinize"):
        if not shutil.which(tool):
            raise RuntimeError(f"Не установлен {tool}. Выполните scripts/setup-linux.sh")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Rootfs must be on a Linux filesystem: NTFS does not preserve special files.
    with tempfile.TemporaryDirectory(prefix="station-mini-root-") as temp:
        work = Path(temp)
        print("Распаковка исходного UBI с сохранением владельцев и прав...", flush=True)
        result = subprocess.run([sys.executable, "-m", "ubireader.scripts.ubireader_extract_files",
                                 "-k", "-o", str(work / "extracted"), str(source)],
                                capture_output=True, text=True)
        if result.returncode or any(t in (result.stdout + result.stderr).lower()
                                    for t in ("fail:", "error:", "fatal", "corrupted")):
            raise RuntimeError("Распаковка UBI завершилась с ошибками:\n" + result.stdout + result.stderr)
        roots = list((work / "extracted").glob("*/rootfs"))
        if len(roots) != 1:
            raise RuntimeError("Ожидался один UBI-том rootfs")
        root = roots[0]
        try:
            marker = patch_rootfs(root, key_text, block_updates)
        except RuntimeError:
            diagnostic = destination.with_suffix(".diagnostics.json")
            try:
                diagnostic.write_text(json.dumps(firmware_diagnostics(root, source_hash),
                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print("Firmware diagnostics saved: " + str(diagnostic), flush=True)
            except (OSError, ValueError) as error:
                print("Could not save firmware diagnostics: " + str(error), flush=True)
            raise
        if marker.get("ota_policy", {}).get("source_policy") == "noupdate-v1":
            print("Recognized the existing noupdate patch; preserving and completing the OTA block.", flush=True)
        if marker.get("ota_policy", {}).get("source_policy") == "stock-updater-switcher-v1":
            print("Recognized the verified updater_switcher; preserving platformd and other services.", flush=True)
        if marker["source_adb_activation"] == "legacy-persistent":
            print("Распознан прежний патч persistent USB ADB; обновляю ключ и настройки USB/OTA.", flush=True)
        ubifs_path = work / "rootfs.ubifs"
        print("Сборка UBIFS и UBI...", flush=True)
        subprocess.run(["mkfs.ubifs", "-r", str(root), "-m", "2048", "-e", "126976",
                        "-c", str(layout["max_lebs"]), "-x", "lzo", "-f", "8", "-k", "r5", "-l", "5",
                        "-p", "1", "-j", "8388608", "-o", str(ubifs_path)], check=True)
        config = work / "ubinize.ini"
        config.write_text("[rootfs]\nmode=ubi\nimage=" + str(ubifs_path) +
                          "\nvol_id=0\nvol_type=dynamic\nvol_name=rootfs\n"
                          f"vol_alignment=1\nvol_size={layout['volume_bytes']}\n", encoding="utf-8")
        image = work / "system-root.ubi"
        subprocess.run(["ubinize", "-o", str(image), "-m", "2048", "-p", "131072",
                        "-s", "2048", "-O", "2048", "-Q", str(layout["image_seq"]), str(config)], check=True)
        size = image.stat().st_size
        if size > SYSTEM_SIZE:
            raise RuntimeError("Образ не помещается в раздел system")
        with image.open("ab") as stream:
            left = SYSTEM_SIZE - size
            while left:
                count = min(left, 1024 * 1024)
                stream.write(b"\xff" * count)
                left -= count
        report = {"format": 2, "model": "YNDX-0004", "partition": "system",
                  "image_type": "normal", "bytes": SYSTEM_SIZE, "source_sha256": source_hash,
                  "image_sha256": sha256(image), "public_key_sha256": hashlib.sha256(key_text.encode()).hexdigest(),
                  "patch": "persistent-usb-adb-v2", "builder": __version__, "layout": layout,
                  "ota_disabled": marker["ota_disabled"],
                  "source_adb_activation": marker["source_adb_activation"],
                  "source_rootless": marker["source_adb_activation"] == "stock-deactivation"}
        with destination.open("xb") as out, image.open("rb") as inp:
            shutil.copyfileobj(inp, out)
        destination.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Готово: " + str(destination), flush=True)
        return report


def main():
    parser = argparse.ArgumentParser(description="Offline YNDX-0004 USB ADB image builder")
    parser.add_argument("source", type=Path)
    parser.add_argument("public_key", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--allow-ota", action="store_true")
    args = parser.parse_args()
    build(args.source, args.public_key, args.destination, not args.allow_ota)


if __name__ == "__main__":
    main()
