"""Linux-only offline builder. Runs under fakeroot to retain file metadata."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, mini1
from .mini1 import KNOWN_NOUPDATE_SCRIPT, KNOWN_UPDATER_SWITCHER_HASHES, SYSTEM_SIZE

# UBIFS journal size used by the factory image (8 MiB).
UBIFS_JOURNAL_BYTES = 8 * 1024 * 1024
# Supported rootfs volume size, in physical eraseblocks.
MIN_ROOTFS_PEBS, MAX_ROOTFS_PEBS = 65, 620
ADB_PUBLIC_KEY_BYTES = 524


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular_file(path):
    """A real file, never a symlink that could point outside the rootfs."""
    path = Path(path)
    return not path.is_symlink() and path.is_file()


def validate_public_key(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    try:
        decoded = base64.b64decode(text.split()[0], validate=True)
    except (ValueError, IndexError) as exc:
        raise ValueError("Нужен публичный ADB-ключ .pub, не приватный ключ") from exc
    if len(decoded) != ADB_PUBLIC_KEY_BYTES or int.from_bytes(decoded[:4], "little") != 64:
        raise ValueError("Неверный формат публичного RSA-2048 ADB-ключа")
    return text + "\n"


def inspect_layout(source):
    from ubireader.ubi import ubi
    from ubireader.ubi_io import leb_virtual_file, ubi_file
    from ubireader.ubifs import ubifs

    handle = ubi_file(str(source), mini1.UBI_PEB_SIZE)
    try:
        container = ubi(handle)
        if (
            container.min_io_size != mini1.UBI_MIN_IO
            or container.leb_size != mini1.UBI_LEB_SIZE
            or len(container.images) != 1
        ):
            raise ValueError("Unsupported UBI geometry / multiple images")
        image = container.images[0]
        if set(image.volumes) != {"rootfs"}:
            raise ValueError("Expected one rootfs volume")
        volume = image.volumes["rootfs"]
        if volume.vol_id != 0 or volume.vol_rec.vol_type != 1:
            raise ValueError("Expected dynamic rootfs volume 0")
        count = volume.vol_rec.reserved_pebs
        superblock = ubifs(leb_virtual_file(container, volume.get_blocks(container.blocks))).superblock_node
        if not MIN_ROOTFS_PEBS <= count <= MAX_ROOTFS_PEBS or superblock.fmt_version != 4 or superblock.key_hash != 0:
            raise ValueError("Unsupported UBIFS layout")
        return {
            "volume_bytes": count * mini1.UBI_LEB_SIZE,
            "max_lebs": superblock.max_leb_cnt,
            "image_seq": image.image_seq,
        }
    finally:
        handle.close()


def launcher_tasks(value):
    """Every task object anywhere in launch.json."""
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
    """The verified updater_switcher task, next to the platformd main task."""
    if (
        task.get("name") != "updater_switcher"
        or task.get("path") != mini1.UPDATER_SWITCHER
        or task.get("arg") != mini1.SWITCHER_ARGS
    ):
        return False
    if not any(mini1.is_main_task(t, "--platformd") for t in tasks):
        return False
    binary = Path(root) / mini1.UPDATER_SWITCHER.lstrip("/")
    return regular_file(binary) and sha256(binary) in KNOWN_UPDATER_SWITCHER_HASHES


def firmware_diagnostics(root, source_hash):
    """Export only version/check results and hashes, never account configs or keys."""
    root = Path(root)
    vendor = root / mini1.QUASAR
    report = {
        "format": 1,
        "builder_version": __version__,
        "source_sha256": source_hash,
        "stage": "rootfs-patch-failed",
        "firmware_written": False,
    }
    for name in ("version_full", "launch.json", "updater_switcher"):
        path = vendor / name
        if not regular_file(path) or not path.resolve().is_relative_to(root.resolve()):
            report[name] = {"present_regular_file": False}
            continue
        report[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        if name == "version_full" and path.stat().st_size <= 256:
            value = path.read_text(encoding="utf-8", errors="replace").strip()
            if re.fullmatch(r"[A-Za-z0-9._-]{1,150}", value):
                report[name]["version"] = value
    if report["launch.json"].get("present_regular_file") is False:
        return report
    try:
        tasks = list(launcher_tasks(json.loads((vendor / "launch.json").read_text(encoding="utf-8"))))
    except (ValueError, RuntimeError):
        report["launch_parse_failed"] = True
        return report
    report["platformd_main_matches"] = any(mini1.is_main_task(t, "--platformd") for t in tasks)
    report["switcher_checks"] = [
        {
            "name_matches": t.get("name") == "updater_switcher",
            "path_matches": t.get("path") == mini1.UPDATER_SWITCHER,
            "arguments_match": t.get("arg") == mini1.SWITCHER_ARGS,
        }
        for t in tasks
        if mini1.is_updater_task(t)
    ]
    report["switcher_hash_matches"] = report["updater_switcher"].get("sha256") in KNOWN_UPDATER_SWITCHER_HASHES
    return report


def _well_formed(task):
    arguments = task.get("arg", [])
    return (
        isinstance(task, dict)
        and isinstance(task.get("path", ""), str)
        and isinstance(arguments, list)
        and all(isinstance(arg, str) for arg in arguments)
    )


def _starts_updater(task):
    arguments = task.get("arg", [])
    names = {task.get("name"), Path(task.get("path", "")).name, *(Path(arg).name for arg in arguments)}
    updatesd = any(arg == "--updatesd" or arg.startswith("--updatesd=") for arg in arguments)
    return updatesd or bool(names & mini1.UPDATER_NAMES)


def known_noupdate_policy(root, tasks):
    """Recognize the previously installed noupdate patch, not arbitrary absence.

    Its exact inert apply script and the normal Mini 1 main task must both be
    present, and no task may still start an updater.
    """
    script = Path(root) / mini1.QUASAR / "device_ota_update.sh"
    if not regular_file(script) or script.read_bytes().replace(b"\r\n", b"\n") != KNOWN_NOUPDATE_SCRIPT:
        return False
    if not tasks or not all(_well_formed(task) for task in tasks):
        return False
    if any(_starts_updater(task) for task in tasks):
        return False
    return any(mini1.is_main_task(task, "--yandexmini") for task in tasks)


def _verified_switchers(root, tasks):
    """IDs of updater tasks; every one must be the verified switcher."""
    switchers = set()
    for task in filter(mini1.is_updater_task, tasks):
        if not known_updater_switcher(root, task, tasks):
            raise RuntimeError("Unknown updater task or switcher binary; OTA blocking is not confirmed")
        switchers.add(id(task))
    return switchers


def _drop_ota_tasks(data, switchers):
    """Remove updatesd and verified switcher tasks in place; return (removed, retained)."""
    removed, retained = [], []

    def is_ota(task):
        return isinstance(task, dict) and (
            id(task) in switchers or task.get("name") == "updatesd" or "--updatesd" in task.get("arg", [])
        )

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return
        for key, child in list(value.items()):
            if key == "tasks" and isinstance(child, list):
                kept = [task for task in child if not is_ota(task)]
                removed.extend(task.get("name", "updatesd") for task in child if is_ota(task))
                retained.extend(kept)
                value[key] = kept
            visit(value[key])

    visit(data)
    return removed, retained


def _block_entrypoints(root, entrypoints):
    """Replace OTA entry points; never follow a symlink into a shared multicall binary."""
    blocked = []
    for path in entrypoints:
        if path.is_symlink():
            path.unlink()
        path.write_text('#!/bin/sh\necho "OTA disabled by Station Mini Root" >&2\nexit 78\n', encoding="utf-8")
        path.chmod(0o755)
        blocked.append("/" + path.relative_to(root).as_posix())
    return blocked


def disable_ota(root):
    root = Path(root)
    launch = root / mini1.QUASAR / "launch.json"
    data = json.loads(launch.read_text(encoding="utf-8"))
    switchers = _verified_switchers(root, list(launcher_tasks(data)))
    removed, retained = _drop_ota_tasks(data, switchers)
    noupdate = not removed and known_noupdate_policy(root, retained)
    if not removed and not noupdate:
        raise RuntimeError("Cannot identify updatesd in launch.json; refusing to claim OTA is blocked")
    entrypoints = [root / rel for rel in mini1.OTA_ENTRYPOINTS if (root / rel).is_file() or (root / rel).is_symlink()]
    if not entrypoints:
        raise RuntimeError("No known OTA entry point was found")
    launch.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    if noupdate:
        policy = "noupdate-v1"
    elif switchers:
        policy = "stock-updater-switcher-v1"
    else:
        policy = "stock-updatesd"
    return {
        "removed_tasks": removed,
        "blocked_entrypoints": _block_entrypoints(root, entrypoints),
        "source_policy": policy,
    }


def patch_adb_activation(content):
    if "Keep persistent USB ADB" in content:
        raise RuntimeError("This source is already patched. Restore a pre-root backup before reinstalling.")
    stock = re.compile(
        r'(?m)^[ \t]*killall adbd\s*\n[ \t]*mv "\$\{ADBD\}" "\$\{ADBD_BACKUP\}"\s*\n'
        r"[ \t]*sync\s*\n[ \t]*echo[^\n]*Stop adb[^\n]*"
    )
    if len(stock.findall(content)) == 1:
        return stock.sub('    echo "`date`: Keep persistent USB ADB" >> "${ADB_LOG}"', content), "stock-deactivation"
    # The owner's earlier method already replaced the final deactivation
    # branch with this exact log-only branch. Recognize that branch explicitly;
    # do not treat every script without `killall adbd` as compatible.
    legacy = re.compile(
        r'(?m)^else[ \t]*\n[ \t]*echo "`date`: Ignore adb deactivation \(persistent USB ADB\)" '
        r'>> "\$\{ADB_LOG\}"[ \t]*\nfi[ \t]*\n?\Z'
    )
    if (
        not stock.findall(content)
        and len(legacy.findall(content)) == 1
        and content.count('if [ "$ARG_CMD" = "activate" ]; then') == 1
        and not re.search(r'\bkillall[ \t]+adbd\b|\bmv[ \t]+"\$\{ADBD\}"', content)
    ):
        return (
            legacy.sub('else\n    echo "`date`: Keep persistent USB ADB" >> "${ADB_LOG}"\nfi\n', content),
            "legacy-persistent",
        )
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
    script = root / mini1.QUASAR / "activate_adb.sh"
    content = script.read_text(encoding="utf-8")
    if (root / mini1.ROOT_MARKER).exists() or "Keep persistent USB ADB" in content:
        raise RuntimeError("This source is already patched. Restore a pre-root backup before reinstalling.")
    content, activation_source = patch_adb_activation(content)
    script.write_text(content, encoding="utf-8", newline="\n")
    script.chmod(0o755)
    gadget = root / "etc/init.d/S89usbgadget"
    content = gadget.read_text(encoding="utf-8")
    network_adb = 'if [ -f "/data/quasar_activate_network_adb" ]; then'
    if content.count(network_adb) != 1:
        raise RuntimeError("Неизвестный S89usbgadget; патч остановлен")
    gadget.write_text(
        content.replace(network_adb, "if false; then # Station Mini Root: USB mode"), encoding="utf-8", newline="\n"
    )
    gadget.chmod(0o755)
    # Make absence of essential symlinks/modes a build error, not a bricked device.
    for rel in ("bin/busybox", "sbin/init", "etc/init.d/rcS"):
        path = root / rel
        if not path.is_symlink() and (not path.is_file() or not path.stat().st_mode & 0o111):
            raise RuntimeError(f"Потеряны права/файл: {rel}")
    if not (root / "etc/init.d/S99onboot.sh").is_symlink():
        raise RuntimeError("Потеряна символическая ссылка S99onboot.sh")
    marker = {
        "tool": "StationMiniRoot",
        "version": __version__,
        "ota_disabled": bool(block_updates),
        "ota_policy": disable_ota(root) if block_updates else {},
        "source_adb_activation": activation_source,
    }
    (root / mini1.ROOT_MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    (root / mini1.ROOT_MARKER).chmod(0o644)
    return marker


RECOGNIZED_SOURCES = {
    "noupdate-v1": "Recognized the existing noupdate patch; preserving and completing the OTA block.",
    "stock-updater-switcher-v1": "Recognized the verified updater_switcher; preserving platformd and other services.",
}


def _extract_rootfs(source, work):
    print("Распаковка исходного UBI с сохранением владельцев и прав...", flush=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ubireader.scripts.ubireader_extract_files",
            "-k",
            "-o",
            str(work / "extracted"),
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode or any(word in output.lower() for word in ("fail:", "error:", "fatal", "corrupted")):
        raise RuntimeError("Распаковка UBI завершилась с ошибками:\n" + output)
    roots = list((work / "extracted").glob("*/rootfs"))
    if len(roots) != 1:
        raise RuntimeError("Ожидался один UBI-том rootfs")
    return roots[0]


def _patch_with_diagnostics(root, key_text, block_updates, source_hash, destination):
    """Patch the rootfs; on an unknown firmware save a privacy-safe diagnostic first."""
    try:
        marker = patch_rootfs(root, key_text, block_updates)
    except RuntimeError:
        diagnostic = destination.with_suffix(".diagnostics.json")
        try:
            diagnostic.write_text(
                json.dumps(firmware_diagnostics(root, source_hash), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print("Firmware diagnostics saved: " + str(diagnostic), flush=True)
        except (OSError, ValueError) as error:
            print("Could not save firmware diagnostics: " + str(error), flush=True)
        raise
    message = RECOGNIZED_SOURCES.get(marker["ota_policy"].get("source_policy"))
    if message:
        print(message, flush=True)
    if marker["source_adb_activation"] == "legacy-persistent":
        print("Распознан прежний патч persistent USB ADB; обновляю ключ и настройки USB/OTA.", flush=True)
    return marker


def _make_ubi(root, layout, work):
    """Pack the rootfs into a full-size system image with the factory geometry."""
    print("Сборка UBIFS и UBI...", flush=True)
    ubifs_path = work / "rootfs.ubifs"
    subprocess.run(
        [
            "mkfs.ubifs",
            "-r",
            str(root),
            "-m",
            str(mini1.UBI_MIN_IO),
            "-e",
            str(mini1.UBI_LEB_SIZE),
            "-c",
            str(layout["max_lebs"]),
            "-x",
            "lzo",
            "-f",
            "8",
            "-k",
            "r5",
            "-l",
            "5",
            "-p",
            "1",
            "-j",
            str(UBIFS_JOURNAL_BYTES),
            "-o",
            str(ubifs_path),
        ],
        check=True,
    )
    config = work / "ubinize.ini"
    config.write_text(
        f"[rootfs]\nmode=ubi\nimage={ubifs_path}\nvol_id=0\nvol_type=dynamic\nvol_name=rootfs\n"
        f"vol_alignment=1\nvol_size={layout['volume_bytes']}\n",
        encoding="utf-8",
    )
    image = work / "system-root.ubi"
    subprocess.run(
        [
            "ubinize",
            "-o",
            str(image),
            "-m",
            str(mini1.UBI_MIN_IO),
            "-p",
            str(mini1.UBI_PEB_SIZE),
            "-s",
            str(mini1.UBI_MIN_IO),
            "-O",
            str(mini1.UBI_MIN_IO),
            "-Q",
            str(layout["image_seq"]),
            str(config),
        ],
        check=True,
    )
    size = image.stat().st_size
    if size > SYSTEM_SIZE:
        raise RuntimeError("Образ не помещается в раздел system")
    # Erased NAND reads as 0xFF; pad to the full partition.
    with image.open("ab") as stream:
        left = SYSTEM_SIZE - size
        while left:
            count = min(left, 1024 * 1024)
            stream.write(b"\xff" * count)
            left -= count
    return image


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
        root = _extract_rootfs(source, work)
        marker = _patch_with_diagnostics(root, key_text, block_updates, source_hash, destination)
        image = _make_ubi(root, layout, work)
        report = {
            "format": 2,
            "model": mini1.MODEL,
            "partition": "system",
            "image_type": "normal",
            "bytes": SYSTEM_SIZE,
            "source_sha256": source_hash,
            "image_sha256": sha256(image),
            "public_key_sha256": hashlib.sha256(key_text.encode()).hexdigest(),
            "patch": "persistent-usb-adb-v2",
            "builder": __version__,
            "layout": layout,
            "ota_disabled": marker["ota_disabled"],
            "source_adb_activation": marker["source_adb_activation"],
            "source_rootless": marker["source_adb_activation"] == "stock-deactivation",
        }
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
