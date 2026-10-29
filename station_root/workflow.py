from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .image_builder import sha256, validate_public_key
from .usb_transport import Transport
from .adb import NO_WINDOW, AdbSession, device_serials, is_station, run_hidden
from .mini1 import MODEL, ROOT_MARKER, STOCK_SYSTEM_SHA256, SYSTEM_SIZE
from .environment import (
    prepare_environment,
    builder_ready,
    installed_distro,
    wsl_prefix,
    MANAGED_PYTHON,
    run_capture,
    external_process,
)
from . import homebrew
from .process_output import iter_output


# Seconds to wait for the speaker to boot and expose ADB root after flashing.
ROOT_BOOT_TIMEOUT = 180
KEYGEN_TIMEOUT = 30
WSL_PATH_TIMEOUT = 90


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def validate_image(image, public_key=None):
    image = Path(image)
    report = json.loads(image.with_suffix(".json").read_text(encoding="utf-8"))
    required = {
        "format": 2,
        "model": MODEL,
        "partition": "system",
        "image_type": "normal",
        "bytes": SYSTEM_SIZE,
        "patch": "persistent-usb-adb-v2",
    }
    if any(report.get(k) != v for k, v in required.items()):
        raise ValueError("Образ не соответствует поддерживаемому профилю YNDX-0004")
    source_digest = report.get("source_sha256", "")
    if len(source_digest) != 64 or any(c not in "0123456789abcdef" for c in source_digest):
        raise ValueError("Invalid source hash in image report")
    if image.stat().st_size != SYSTEM_SIZE or sha256(image) != report.get("image_sha256"):
        raise ValueError("Образ повреждён: размер или SHA-256 не совпадает с отчётом")
    with image.open("rb") as stream:
        if stream.read(4) != b"UBI#":
            raise ValueError("Это не полный UBI-контейнер")
    if public_key:
        import hashlib

        digest = hashlib.sha256(validate_public_key(public_key).encode()).hexdigest()
        if digest != report.get("public_key_sha256"):
            raise ValueError("Образ собран с другим ADB-ключом")
    return report


class Workflow:
    def __init__(self, log=print, progress=lambda n, total: None):
        self.log, self.progress = log, progress
        self.transport = None
        self.cancel = threading.Event()
        self.phase = lambda phase: None
        self._heartbeat_lock = threading.Lock()

    def connect(self):
        self.close()
        self.cancel.clear()
        self.phase("waiting")
        self.transport = Transport.wait(self.cancel, self.log)
        self.phase("captured")

    def close(self):
        if self.transport:
            try:
                self.transport.close()
            finally:
                self.transport = None

    def heartbeat(self):
        with self._heartbeat_lock:
            if self.transport:
                self.transport.keepalive()

    def host_operation(self, operation):
        """Keep an existing anchor while host-only WSL/ADB/filesystem work waits.

        Join before returning so no polling can overlap a partition transfer.
        """
        if self.transport is None:
            return operation()
        stopped, errors = threading.Event(), []

        def poll():
            while not stopped.wait(0.4):
                try:
                    self.heartbeat()
                except Exception as exc:
                    errors.append(exc)
                    return

        thread = threading.Thread(target=poll, daemon=True)
        thread.start()
        try:
            result = operation()
        finally:
            stopped.set()
            thread.join()
        if errors:
            raise RuntimeError("USB connection lost during host preparation: " + str(errors[0]))
        return result

    def setup(self, config):
        return prepare_environment(config, self.log, self._build_process, resource_root())

    def ensure_environment(self, config, setup_if_missing=True):
        if sys.platform == "win32":
            # Dependency setup already selected this distro. Root must not
            # launch every other WSL installation just to rediscover it.
            distro = config.get("distro", "").strip()
            if not distro:
                distro = installed_distro()
            if not distro or not builder_ready(distro, strict=True, log=self.log):
                if not setup_if_missing:
                    raise RuntimeError("Run dependency setup first; the selected WSL builder is missing.")
                distro = self.setup(config)
                if not builder_ready(distro, strict=True, log=self.log):
                    raise RuntimeError("Run dependency setup first; the selected WSL builder is missing.")
            config["distro"] = distro
        return config

    @staticmethod
    def save_session(work, **fields):
        path = Path(work) / "session.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        previous.update(fields)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)

    def install(self, config):
        """One operation: backup → flash → root → Homebrew; stock Alice stays enabled."""
        self.adb_binary(config)  # Fail before any device operation if host ADB is missing.
        homebrew.resources(resource_root())  # Missing bundles must fail before any flashing.
        session_path = Path(config["work"]) / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8")) if session_path.exists() else {}
        if session.get("state") in ("homebrew-pending", "root-verified", "root-and-homebrew-verified"):
            retry = dict(config, serial=session.get("serial") or config.get("serial", ""))
            retry["serial"] = self.verify(retry, expected_ota=session.get("ota_disabled"))
            self.save_session(config["work"], serial=retry["serial"])
            self.finish_homebrew(retry)
            return
        self.host_operation(lambda: self.ensure_environment(config, setup_if_missing=False))
        if self.transport is None:
            self.connect()
        backup, digest = self.backup(config["work"])
        # Keep a previous working rollback target if a reinstall fails to prepare.
        self.save_session(
            config["work"], pending_backup=str(backup), pending_source_sha256=digest, pending_state="backed-up"
        )
        job = dict(config, source=str(backup))
        image = self.host_operation(lambda: self.prepare(job))
        report = validate_image(image)
        if report["source_sha256"] != digest:
            raise ValueError("Prepared image does not match this backup")
        self.save_session(
            config["work"],
            original_backup=str(backup),
            source_sha256=digest,
            image=str(image),
            state="prepared",
            restore_eligible=True,
            ota_disabled=report["ota_disabled"],
        )
        self.flash(dict(job, image=str(image)), original=(backup, digest))
        self.save_session(config["work"], state="written-and-verified")
        self.reboot()
        self.log("Waiting for ADB after reboot...")
        deadline = time.monotonic() + ROOT_BOOT_TIMEOUT
        last_error = ""
        while time.monotonic() < deadline:
            try:
                serial = self.verify(config, expected_ota=report["ota_disabled"])
                break
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                self.cancel.wait(2)
        else:
            raise RuntimeError(
                "Firmware was written and read-back verified, but ADB root was not confirmed. " + last_error
            )
        self.save_session(config["work"], state="root-verified", serial=serial)
        self.finish_homebrew(dict(config, serial=serial))

    def update_homebrew(self, config):
        """Maintenance for an already rooted speaker; never captures or flashes."""
        from .dependencies import find_adb

        retry = dict(config)
        retry["adb"] = find_adb(retry.get("adb", ""))
        if not retry["adb"]:
            raise RuntimeError("Run dependency setup first; ADB is missing.")
        if not (Path(retry["work"]) / "keys/adbkey").is_file():
            raise RuntimeError("The original ADB private key is missing. Select the existing root workspace.")
        homebrew.resources(resource_root())
        retry["serial"] = self.verify(retry)
        self.finish_homebrew(retry)

    def finish_homebrew(self, config):
        self.phase("homebrew")
        self.log("Installing Homebrew on the speaker...")
        self.save_session(config["work"], state="homebrew-pending")
        try:
            url = homebrew.install(resource_root(), dict(config, adb=self.adb_binary(config)), self.log)
        except Exception as exc:
            self.save_session(config["work"], homebrew_error=str(exc))
            raise RuntimeError(
                "ADB root is confirmed. Homebrew installation failed: "
                + str(exc)
                + " Retry Install root to finish Homebrew without rewriting firmware."
            ) from exc
        self.save_session(config["work"], state="root-and-homebrew-verified", homebrew_url=url, homebrew_error="")
        self.log("Installation complete. Root and Homebrew verified.")
        self.log("Homebrew: " + url)

    def device(self):
        if self.transport is None:
            raise RuntimeError("Сначала нажмите «Поймать станцию» и подключите USB")
        return self.transport

    def backup(self, work):
        self.phase("backup")
        work = Path(work).resolve()
        work.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        output = work / f"system-backup-{stamp}.bin"
        self.log("Чтение system (80 МиБ), резервная копия: " + str(output))
        try:
            digest = self.device().read_system(output, self.progress)
        except Exception:
            # A partial file is kept for diagnosis and never treated as a valid backup.
            if output.exists():
                output.rename(output.with_suffix(".partial"))
            raise
        output.with_suffix(".json").write_text(
            json.dumps(
                {
                    "partition": "system",
                    "bytes": SYSTEM_SIZE,
                    "sha256": digest,
                    "identify": self.device().identify().hex(),
                    "complete": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.log("Копия сохранена. SHA-256: " + digest)
        return output, digest

    @staticmethod
    def adb_binary(config):
        adb = config.get("adb", "").strip() or shutil.which("adb")
        if not adb or not Path(adb).is_file():
            raise RuntimeError("Укажите adb из Android SDK Platform-Tools")
        return str(Path(adb).resolve())

    @staticmethod
    def _workspace_key(work, adb):
        """Create the workspace ADB key once; return its validated public half."""
        key = work / "keys" / "adbkey"
        key.parent.mkdir(parents=True, exist_ok=True)
        if not key.exists():
            run_hidden([adb, "keygen", str(key)], check=True, capture_output=True, timeout=KEYGEN_TIMEOUT)
            if os.name != "nt":
                key.chmod(0o600)
        public = key.with_suffix(".pub")
        validate_public_key(public)
        return public

    def _builder_command(self, config, source, public, output):
        """Command that runs scripts/build_image.sh in Linux, or in WSL on Windows."""
        script = resource_root() / "scripts" / "build_image.sh"
        if sys.platform != "win32":
            cmd = ["bash", str(script), str(source), str(public), str(output)]
            if config.get("builder_python"):
                cmd = ["env", "STATION_ROOT_PYTHON=" + config["builder_python"], *cmd]
            return cmd
        self.ensure_environment(config, setup_if_missing=False)
        prefix = wsl_prefix(config["distro"])

        def linux_path(path):
            # Pass literal argv: no shell expansion of paths or user input.
            code, out, error = run_capture(prefix + ["wslpath", "-a", "-u", str(path)], timeout=WSL_PATH_TIMEOUT)
            if code:
                raise RuntimeError("WSL path conversion failed: " + (error.strip() or out.strip()))
            return out.strip()

        return prefix + [
            "env",
            "STATION_ROOT_PYTHON=" + MANAGED_PYTHON,
            "bash",
            *(linux_path(path) for path in (script, source, public, output)),
        ]

    def prepare(self, config):
        self.phase("build")
        source = Path(config["source"]).resolve()
        work = Path(config["work"]).resolve()
        work.mkdir(parents=True, exist_ok=True)
        if source.stat().st_size != SYSTEM_SIZE:
            raise ValueError("Expected a complete 80 MiB system backup")
        digest = sha256(source)
        public = self._workspace_key(work, self.adb_binary(config))
        block_updates = bool(config.get("block_updates", True))
        output = work / f"system-root-{digest[:16]}-{'no-ota' if block_updates else 'ota'}.ubi"
        if output.exists():
            report = validate_image(output, public)
            if report["source_sha256"] != digest or report.get("ota_disabled") != block_updates:
                raise ValueError("Existing image has different build settings")
            self.log("Образ уже подготовлен и проверен: " + str(output))
            return output
        cmd = self._builder_command(config, source, public, output)
        if not block_updates:
            cmd.append("--allow-ota")
        self.log("Подготовка образа в Linux / WSL. Личный ключ остаётся в рабочей папке.")
        self._build_process(cmd)
        validate_image(output, public)
        self.log("Образ готов: " + str(output))
        return output

    def _build_process(self, cmd):
        lines = queue.Queue()
        tail = []
        environment = os.environ.copy()
        self.log("Command: " + subprocess.list2cmdline([str(part) for part in cmd]))
        process = external_process(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, creationflags=NO_WINDOW
        )

        def reader():
            for line in iter_output(process.stdout):
                lines.put(line.rstrip())
            process.stdout.close()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            while process.poll() is None or thread.is_alive() or not lines.empty():
                try:
                    line = lines.get(timeout=0.2)
                    self.log(line)
                    if line:
                        tail.append(line)
                        del tail[:-8]
                except queue.Empty:
                    pass
                self.heartbeat()
            if process.returncode:
                raise RuntimeError(
                    "Сборка образа остановлена. Запись прошивки не начиналась. "
                    f"Exit code: {process.returncode}\n" + "\n".join(tail)
                )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

    def flash(self, config, original=None):
        self.phase("write")
        work = Path(config["work"]).resolve()
        image = Path(config.get("image", work / "system-root.ubi"))
        report = validate_image(image, work / "keys/adbkey.pub")
        # Re-read this connected station, not an unrelated backup from disk.
        # This both checks the supported firmware and guarantees a fresh backup.
        backup, digest = original if original else self.backup(work)
        if digest != report["source_sha256"]:
            raise ValueError("The prepared image belongs to a different source backup. Nothing was written.")
        if sha256(image) != report["image_sha256"]:
            raise ValueError("Образ изменился после проверки")
        self.log("Запись только system. Не отключайте питание до завершения проверки.")
        self.device().write_system(image, self.progress)
        readback = work / f"readback-{datetime.now():%Y%m%d-%H%M%S-%f}.bin"
        self.log("Обратное чтение 80 МиБ и проверка SHA-256...")
        actual = self.device().read_system(readback, self.progress)
        if actual != report["image_sha256"]:
            raise RuntimeError(
                "SHA-256 записанного system не совпал. Не перезагружайте станцию. "
                "Сохранены исходная копия и обратное чтение: " + str(readback)
            )
        self.log("Запись и обратное чтение совпали. Можно нажать «Перезагрузить», затем «Проверить root».")

    def restore(self, config, backup):
        backup = Path(backup).resolve()
        digest = sha256(backup)
        if backup.stat().st_size != SYSTEM_SIZE:
            raise ValueError("Expected a complete 80 MiB system backup")
        if digest != STOCK_SYSTEM_SHA256:
            metadata = json.loads(backup.with_suffix(".json").read_text(encoding="utf-8"))
            if (
                not metadata.get("complete")
                or metadata.get("partition") != "system"
                or metadata.get("sha256") != digest
            ):
                raise ValueError("Backup checksum/provenance verification failed")
        self.backup(config["work"])
        self.log("Восстановление исходного system...")
        self.device().write_system(backup, self.progress)
        readback = Path(config["work"]) / f"restore-check-{datetime.now():%Y%m%d-%H%M%S-%f}.bin"
        if self.device().read_system(readback, self.progress) != digest:
            raise RuntimeError("Проверка восстановления не прошла. Не перезагружайте станцию.")
        self.log("Исходный system восстановлен и проверен. Можно перезагрузить станцию.")

    def unroot(self, config):
        work = Path(config["work"])
        session = json.loads((work / "session.json").read_text(encoding="utf-8"))
        if not session.get("restore_eligible"):
            raise ValueError("No verified pre-root backup is recorded for this workspace")
        backup = Path(session["original_backup"])
        if sha256(backup) != session["source_sha256"]:
            raise ValueError("Original pre-root backup was changed")
        if self.transport is None:
            self.connect()
        self.restore(config, backup)
        self.save_session(work, state="original-restored")
        self.reboot()
        self.log("Original firmware restored, including its original update settings.")

    def reboot(self):
        self.device().reboot()
        self.close()
        self.log("Отправлен reset. Дождитесь обычной загрузки и появления ADB.")

    def verify(self, config, expected_ota=None):
        """Confirm ADB root on the speaker; return its ADB serial."""
        key = Path(config["work"]).resolve() / "keys/adbkey"
        if not key.is_file():
            raise FileNotFoundError("Нет личного ключа. Укажите рабочую папку, где собирался образ.")
        session = AdbSession.open(self.adb_binary(config), key, config.get("serial", ""))
        try:
            serial = self._select_station(session, config)
            # Identify the board before reporting root for an arbitrary Android phone.
            if not is_station(session.shell(serial, "cat /proc/device-tree/compatible")):
                raise RuntimeError("ADB-устройство не сообщает Amlogic compatible; проверьте серийный номер.")
            identity = session.shell(serial, "id")
            self.log(identity.strip())
            if not {"uid=0(root)", "uid=0"} & set(identity.split()):
                raise RuntimeError("ADB доступен, но shell не имеет uid=0")
            if expected_ota:
                self._check_ota_policy(session, serial)
            self.log("ROOT ПОДТВЕРЖДЁН: " + serial)
            return serial
        finally:
            session.close()

    def _select_station(self, session, config):
        listing = session.run("devices", "-l")
        devices = device_serials(listing)
        if (
            not devices
            and sys.platform == "win32"
            and config.get("_managed_dependencies")
            and not config.get("_adb_driver_repair_attempted")
        ):
            # The normal ADB interface appears only after boot; repair only
            # this speaker's actual ADB function, never its RNDIS parent.
            from .dependencies import install_driver

            config["_adb_driver_repair_attempted"] = True
            install_driver(resource_root(), "adb", self.log)
            raise RuntimeError("ADB driver prepared. Waiting for the speaker...")
        serial = config.get("serial", "").strip()
        if serial:
            if serial not in devices:
                raise RuntimeError("Выбранное устройство не готово. Список:\n" + listing)
            return serial
        if len(devices) > 1:
            devices = session.stations(devices)
        if len(devices) != 1:
            raise RuntimeError("Укажите серийный номер ADB. Список:\n" + listing)
        return devices[0]

    def _check_ota_policy(self, session, serial):
        marker = json.loads(session.shell(serial, "cat /" + ROOT_MARKER))
        if marker.get("ota_disabled") is not True:
            raise RuntimeError("Root works, but the OTA policy marker is missing")
        if any("--updatesd" in line for line in session.shell(serial, "ps").splitlines()):
            raise RuntimeError("Root works, but updatesd is still running")
        self.log("OTA policy verified: updatesd is not running.")
