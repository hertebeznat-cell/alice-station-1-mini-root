"""Deploy the bundled Homebrew service after ADB root has been verified.

This step never changes the stock Alice startup hook or removes other apps.
"""
import importlib.util
from pathlib import Path


def resources(root):
    root = Path(root) / "alice-homebrew"
    installer = root / "scripts/install-speaker.py"
    binary = root / "dist/alice-homebrew-linux-arm64"
    required = [installer, binary, root / "scripts/start-speaker.sh", root / "scripts/S98alice-homebrew"]
    for path in required:
        if not path.is_file():
            raise RuntimeError("Bundled Homebrew component is missing: " + str(path))
    with binary.open("rb") as stream:
        header = stream.read(20)
    if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
        raise RuntimeError("Bundled Homebrew binary is not Linux ARM64")
    return installer, binary


def install(root, config, log=print):
    installer, binary = resources(root)
    spec = importlib.util.spec_from_file_location("alice_homebrew_installer", installer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = ["--adb", config["adb"], "--key", str(Path(config["work"]) / "keys/adbkey"),
            "--binary", str(binary), "--install", "--autostart"]
    if config.get("serial"):
        args.extend(["--serial", config["serial"]])
    return module.main(args, log=log)
