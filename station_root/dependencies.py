"""Automatic local tool paths, verified ADB downloads and dependency readiness."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from .environment import builder_ready, discover_distros, run_capture, MANAGED_PYTHON, run_elevated
from .usb_transport import make_backend, USBBackendError

SDK_METADATA = "https://dl.google.com/android/repository/repository2-1.xml"


def data_root():
    override = os.environ.get("STATION_ROOT_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "StationMiniRoot"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "StationMiniRoot"


def default_workspace():
    legacy = Path.home() / "StationMiniRoot"
    return legacy if (legacy / "session.json").is_file() else data_root() / "work"


def valid_adb(path):
    if not path or not Path(path).is_file():
        return False
    try:
        code, output, _ = run_capture([str(path), "version"], timeout=15)
        return code == 0 and "Android Debug Bridge version" in output
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_adb(saved=""):
    candidates = [saved, shutil.which("adb")]
    root = data_root() / "tools"
    if root.exists():
        candidates.extend(str(p) for p in root.glob("platform-tools-*/platform-tools/" +
                          ("adb.exe" if sys.platform == "win32" else "adb")))
    return next((str(Path(p).resolve()) for p in candidates if valid_adb(p)), "")


def platform_tools_archive(xml_data, host):
    root = ET.fromstring(xml_data)
    # Strip XML namespaces so Google's schema namespace version can change.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    for package in root.findall(".//remotePackage"):
        if package.get("path") != "platform-tools":
            continue
        channel = package.find("channelRef")
        if channel is not None and channel.get("ref") != "channel-0":
            continue
        for archive in package.findall("./archives/archive"):
            if archive.findtext("host-os") != host:
                continue
            complete = archive.find("complete")
            checksum = complete.find("checksum") if complete is not None else None
            if complete is None or checksum is None:
                continue
            algorithm = checksum.get("type", "sha1").replace("-", "").lower()
            digest = (checksum.text or "").strip().lower()
            if algorithm not in ("sha1", "sha256") or len(digest) != hashlib.new(algorithm).digest_size * 2:
                raise ValueError("Invalid Platform-Tools checksum in Google metadata")
            int(digest, 16)
            url = urllib.parse.urljoin("https://dl.google.com/android/repository/", complete.findtext("url", ""))
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "dl.google.com":
                raise ValueError("Unexpected Platform-Tools download host")
            size = int(complete.findtext("size", "0"))
            if not 0 < size <= 128 * 1024 * 1024:
                raise ValueError("Invalid Platform-Tools archive size")
            return {"url": url, "algorithm": algorithm, "digest": digest, "bytes": size}
    raise RuntimeError("No stable Platform-Tools archive was found for this computer")


def extract_tools(archive_path, destination):
    destination = Path(destination)
    with zipfile.ZipFile(archive_path) as archive:
        if sum(member.file_size for member in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Platform-Tools extraction size is too large")
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.filename or
                    ":" in member.filename or not path.parts or path.parts[0] != "platform-tools" or
                    stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("Unsafe path in Platform-Tools archive")
        archive.extractall(destination)


def download_adb(log=print, progress=lambda n, total: None):
    host = "windows" if sys.platform == "win32" else "linux"
    if host == "linux" and __import__("platform").machine().lower() not in ("x86_64", "amd64"):
        raise RuntimeError("Install adb from your distribution on non-x86 Linux")
    request = urllib.request.Request(SDK_METADATA, headers={"User-Agent": "StationMiniRoot/0.3"})
    with urllib.request.urlopen(request, timeout=60) as response:
        metadata = response.read(8 * 1024 * 1024)
    selected = platform_tools_archive(metadata, host)
    tools = data_root() / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    target = tools / ("platform-tools-" + selected["digest"][:16])
    binary = target / "platform-tools" / ("adb.exe" if sys.platform == "win32" else "adb")
    if valid_adb(binary):
        return str(binary)
    log("Downloading Android Platform-Tools from Google...")
    with tempfile.TemporaryDirectory(prefix="adb-download-", dir=tools) as temporary:
        temporary = Path(temporary)
        archive = temporary / "tools.zip"
        digest = hashlib.new(selected["algorithm"])
        received = 0
        with urllib.request.urlopen(selected["url"], timeout=60) as response, archive.open("xb") as stream:
            while block := response.read(128 * 1024):
                received += len(block)
                if received > selected["bytes"]:
                    raise ValueError("Platform-Tools download exceeded the expected size")
                stream.write(block)
                digest.update(block)
                progress(received, selected["bytes"])
        if received != selected["bytes"] or digest.hexdigest() != selected["digest"]:
            raise ValueError("Platform-Tools download checksum did not match Google metadata")
        extracted = temporary / "extracted"
        extract_tools(archive, extracted)
        extracted_binary = extracted / "platform-tools" / binary.name
        if sys.platform != "win32":
            extracted_binary.chmod(0o755)
        if not valid_adb(extracted_binary):
            raise RuntimeError("Downloaded adb did not pass its version check")
        # A failed old installation never replaces a known working tool directory.
        if target.exists():
            target = tools / (target.name + "-" + __import__("uuid").uuid4().hex[:8])
        extracted.rename(target)
        return str(target / "platform-tools" / binary.name)


def driver_registered():
    if sys.platform != "win32":
        return Path("/etc/udev/rules.d/70-station-mini-root.rules").is_file()
    try:
        import winreg
        path = r"SYSTEM\CurrentControlSet\Enum\USB\VID_1B8E&PID_C003"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as child:
                    try:
                        service = str(winreg.QueryValueEx(child, "Service")[0]).lower()
                    except OSError:
                        continue
                    if service in ("winusb", "libusbk"):
                        return True
    except (ImportError, OSError):
        pass
    return False


def install_driver(resources, mode, log=print):
    if sys.platform != "win32" or mode not in ("boot", "adb"):
        raise ValueError("Unsupported driver setup request")
    helper = Path(resources) / "resources/station-driver-helper.exe"
    manifest = json.loads((helper.with_suffix(".json")).read_text(encoding="utf-8"))
    with helper.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if manifest.get("sha256") != actual:
        raise ValueError("Driver helper integrity check failed")
    dest = data_root() / "drivers" / mode
    dest.mkdir(parents=True, exist_ok=True)
    log("Installing the speaker USB driver. Confirm the Windows administrator prompt.")
    code = run_elevated(str(helper), [mode, str(dest)])
    if code:
        raise RuntimeError("Speaker USB driver installation failed or was declined")


class Dependencies:
    def __init__(self, workflow, resources):
        self.workflow = workflow
        self.resources = Path(resources)

    @property
    def marker(self):
        return data_root() / "dependencies.json"

    def inspect(self, config):
        result = dict(config)
        result["adb"] = find_adb(result.get("adb", ""))
        discovery = discover_distros(result.get("distro", "")) if sys.platform == "win32" else None
        if discovery:
            result["distro"] = discovery["selected"]
        try:
            recorded = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recorded = {}
        if sys.platform == "win32":
            builder = bool(result.get("distro")) and builder_ready(result["distro"])
        else:
            candidates = (MANAGED_PYTHON, str(Path.home() / ".local/share/station-mini-root/venv/bin/python"))
            result["builder_python"] = next((path for path in candidates if Path(path).is_file()), "")
            builder = bool(result["builder_python"]) and run_capture([result["builder_python"], "-c",
                "import ubireader; import shutil; assert shutil.which('mkfs.ubifs'); assert shutil.which('fakeroot')"])[0] == 0
        try:
            make_backend()
            library = True
        except Exception:
            library = False
        # Do not claim a new machine's USB setup is ready merely because a file exists.
        ready = bool(result["adb"] and builder and library and recorded.get("usb_verified")
                     and recorded.get("adb_driver_staged") and driver_registered())
        return {"config": result, "discovery": discovery, "ready": ready,
                "adb": bool(result["adb"]), "builder": bool(builder), "usb": ready}

    def install(self, config):
        config = dict(config)
        config["adb"] = find_adb(config.get("adb", "")) or download_adb(self.workflow.log, self.workflow.progress)
        if sys.platform == "win32":
            self.workflow.ensure_environment(config)
            if not driver_registered():
                install_driver(self.resources, "boot", self.workflow.log)
            install_driver(self.resources, "adb", self.workflow.log)
        else:
            if not self.inspect(config)["builder"] or not driver_registered():
                script = str(self.resources / "scripts/setup-native-linux.sh")
                if os.geteuid() == 0:
                    self.workflow._build_process(["bash", script])
                elif shutil.which("pkexec"):
                    self.workflow._build_process(["pkexec", "/usr/bin/bash", script])
                else:
                    raise RuntimeError("Run sudo bash scripts/setup-native-linux.sh once, then retry dependencies")
            config["builder_python"] = MANAGED_PYTHON
        self.workflow.log("Connect the speaker by USB to finish the dependency check. No firmware will be written.")
        try:
            self.workflow.connect()
        except USBBackendError as exc:
            # A device visible in USB enumeration can still have an unusable
            # driver. Historical registry entries are not proof it can open.
            # Repair only a target-device open failure, once, never a transfer
            # failure or an error while enumerating unrelated USB devices.
            if sys.platform != "win32" or exc.operation != "open_device":
                raise
            self.workflow.close()
            self.workflow.log("The speaker is visible, but Windows cannot open its USB driver. Repairing WinUSB once...")
            install_driver(self.resources, "boot", self.workflow.log)
            self.workflow.log("Disconnect and reconnect the speaker USB cable now. Waiting with a fresh USB context...")
            self.workflow.connect()
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text(json.dumps({"format": 1, "usb_verified": True, "adb_driver_staged": True,
                                          "distro": config.get("distro", "")}), encoding="utf-8")
        self.workflow.log("All dependencies are ready. Root and restore are now available.")
        return config
