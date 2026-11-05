"""Run on each target OS: python scripts/build-app.py. No device access."""

from PyInstaller.archive.readers import CArchiveReader
import os
import subprocess
import sys
import hashlib
import json
import tempfile
from pathlib import Path

if sys.platform == "darwin":
    import tkinter

    if sys.version_info < (3, 13) or tkinter.TkVersion < 8.6:
        raise RuntimeError("macOS builds require Python 3.13+ with Tk 8.6 or newer")

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
# PyInstaller treats missing optional imports as warnings and can otherwise
# produce an EXE that silently depends on a DLL installed on the build machine.
# Verify the exact packaged backend before starting either platform's build.
try:
    import libusb_package
    from station_root.usb_transport import make_backend

    library = libusb_package.get_library_path()
    if not library or not library.is_file():
        raise RuntimeError("libusb-package contains no library for this platform")
    make_backend()
except Exception as exc:
    raise RuntimeError(
        "Cannot package the USB runtime. Install requirements.txt in this "
        "build interpreter before retrying: " + str(exc)
    ) from exc
separator = os.pathsep
platform = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
dist = root / "dist" if platform == "windows" else root / "dist" / platform
if os.environ.get("STATION_ROOT_DIST_DIR"):
    dist = Path(os.environ["STATION_ROOT_DIST_DIR"])
homebrew = root / "alice-homebrew"
if not (homebrew / "dist/alice-homebrew-linux-arm64").is_file():
    raise RuntimeError("Run scripts/prepare-homebrew.py before packaging the root app")
resources = []
clean = [] if os.environ.get("STATION_ROOT_INCREMENTAL") == "1" else ["--clean"]
if platform == "windows":
    if not (root / "resources/station-driver-helper.exe").is_file():
        raise RuntimeError("Build native/station_driver.c first; see native/README.md")
    resources = ["--add-data", f"{root / 'resources'}{separator}resources"]
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        *clean,
        "--onefile",
        "--windowed",
        "--name",
        "StationMiniRoot",
        "--collect-all",
        "libusb_package",
        "--hidden-import",
        "usb.backend.libusb1",
        "--exclude-module",
        "ubireader",
        "--workpath",
        str(root / "build" / platform),
        "--specpath",
        str(root / "build" / "spec" / platform),
        "--distpath",
        str(dist),
        "--add-data",
        f"{root / 'scripts'}{separator}scripts",
        "--add-data",
        f"{root / 'station_root'}{separator}station_root",
        "--add-data",
        f"{root / 'requirements.txt'}{separator}.",
        "--add-data",
        f"{root / 'requirements-linux.txt'}{separator}.",
        "--add-data",
        f"{homebrew / 'scripts'}{separator}alice-homebrew/scripts",
        "--add-data",
        f"{homebrew / 'dist/alice-homebrew-linux-arm64'}{separator}alice-homebrew/dist",
        "--add-data",
        f"{homebrew / 'licenses'}{separator}alice-homebrew/licenses",
        "--add-data",
        f"{homebrew / 'THIRD_PARTY.md'}{separator}alice-homebrew",
        *resources,
        str(root / "station_mini_root.py"),
    ],
    cwd=root,
    check=True,
)

# PyInstaller re-signs macOS dylibs, changing their bytes. Verify presence there
# and test the extracted library below; other platforms also compare its hash.
# Check the actual artifact, not the developer's Python environment. Loading
# libusb initializes a host context but does not enumerate or open USB devices.

binary = dist / ("StationMiniRoot.exe" if platform == "windows" else "StationMiniRoot")
archive = CArchiveReader(str(binary))
member = next((name for name in archive.toc if name.replace("\\", "/") == "libusb_package/" + library.name), None)
if member is None or (
    platform != "macos"
    and hashlib.sha256(archive.extract(member)).digest() != hashlib.sha256(library.read_bytes()).digest()
):
    raise RuntimeError("The built application is missing the expected bundled libusb library")
with tempfile.TemporaryDirectory(prefix="station-root-check-") as temporary:
    report = Path(temporary) / "usb.json"
    check_env = os.environ.copy()
    check_env.pop("PYTHONPATH", None)
    check_env.pop("PYTHONHOME", None)
    subprocess.run([str(binary), "--check-usb", str(report)], check=True, timeout=60, env=check_env)
    result = json.loads(report.read_text(encoding="utf-8"))
    if (
        result.get("ready") is not True
        or result.get("frozen") is not True
        or not result.get("bundle_root")
        or not Path(result["library"]).resolve().is_relative_to(Path(result["bundle_root"]).resolve())
    ):
        raise RuntimeError("The built application's USB self-check failed: " + str(result))
if platform == "macos":
    with tempfile.TemporaryDirectory(prefix="station-root-gui-check-") as temporary:
        report = Path(temporary) / "gui.json"
        check_env = os.environ.copy()
        check_env["STATION_ROOT_DATA_DIR"] = str(Path(temporary) / "data")
        check_env["STATION_ROOT_CONFIG_DIR"] = str(Path(temporary) / "config")
        subprocess.run([str(binary), "--check-gui", str(report)], check=True, timeout=60, env=check_env)
        result = json.loads(report.read_text(encoding="utf-8"))
        if not result.get("mapped") or result.get("buttons") != 3 or result.get("tk", 0) < 8.6:
            raise RuntimeError("The built application's GUI self-check failed: " + str(result))
    print("Built application: GUI event loop and controls PASS", flush=True)
print("Built application: bundled libusb and frozen USB self-check PASS", flush=True)
