"""Run on each target OS: python scripts/build-app.py. No device access."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
separator = os.pathsep
platform = "windows" if sys.platform == "win32" else "linux"
dist = root / "dist" if platform == "windows" else root / "dist/linux"
if os.environ.get("STATION_ROOT_DIST_DIR"):
    dist = Path(os.environ["STATION_ROOT_DIST_DIR"])
homebrew = root / "alice-homebrew"
if not (homebrew / "dist/alice-homebrew-linux-arm64").is_file():
    raise RuntimeError("Build the Homebrew Linux ARM64 binary before packaging the root app")
resources = []
clean = [] if os.environ.get("STATION_ROOT_INCREMENTAL") == "1" else ["--clean"]
if platform == "windows":
    if not (root / "resources/station-driver-helper.exe").is_file():
        raise RuntimeError("Build native/station_driver.c first; see native/README.md")
    resources = ["--add-data", f"{root / 'resources'}{separator}resources"]
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", *clean, "--onefile",
                "--windowed", "--name", "StationMiniRoot", "--collect-all", "libusb_package",
                "--exclude-module", "ubireader",
                "--workpath", str(root / "build" / platform),
                "--specpath", str(root / "build" / "spec" / platform),
                "--distpath", str(dist),
                "--add-data", f"{root / 'scripts'}{separator}scripts",
                "--add-data", f"{root / 'station_root'}{separator}station_root",
                "--add-data", f"{root / 'requirements.txt'}{separator}.",
                "--add-data", f"{root / 'requirements-linux.txt'}{separator}.",
                "--add-data", f"{homebrew / 'scripts'}{separator}alice-homebrew/scripts",
                "--add-data", f"{homebrew / 'dist/alice-homebrew-linux-arm64'}{separator}alice-homebrew/dist",
                *resources, str(root / "station_mini_root.py")], cwd=root, check=True)
