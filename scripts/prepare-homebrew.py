"""Fetch the pinned official Alice Homebrew release that the root app installs.

The release archive is checked against its pinned SHA-256, then only the
installer scripts, the ARM64 service binary and its license notices are
unpacked into alice-homebrew/ (ignored by git). Homebrew sources live in
https://github.com/hertebeznat-cell/alice-homebrew.
"""

import hashlib
import io
import shutil
import urllib.request
import zipfile
from pathlib import Path

VERSION = "6.0.1"
ARCHIVE = f"AliceHomebrew-{VERSION}.zip"
URL = f"https://github.com/hertebeznat-cell/alice-homebrew/releases/download/v{VERSION}/{ARCHIVE}"
# From the release SHA256SUMS.txt; update together with VERSION.
SHA256 = "dc53b0a7701545f987a9a2ccbb8d7fc1eb6e054d5313669df7169bd3a45fb338"
SIZE = 58639482

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "alice-homebrew"
# Archive member -> path under alice-homebrew/.
FILES = {
    "bin/alice-homebrew-linux-arm64": "dist/alice-homebrew-linux-arm64",
    "scripts/install-speaker.py": "scripts/install-speaker.py",
    "scripts/start-speaker.sh": "scripts/start-speaker.sh",
    "scripts/S98alice-homebrew": "scripts/S98alice-homebrew",
    "THIRD_PARTY.md": "THIRD_PARTY.md",
    "LICENSE": "LICENSE",
}
LICENSES = "licenses/"


def download():
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read(SIZE + 1)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError(f"{ARCHIVE}: size or SHA-256 mismatch")
    return data


def unpack(data):
    if TARGET.exists():
        shutil.rmtree(TARGET)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = dict(FILES)
        members.update(
            {name: name for name in archive.namelist() if name.startswith(LICENSES) and not name.endswith("/")}
        )
        for name, target in members.items():
            path = TARGET / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(name))
    (TARGET / "VERSION").write_text(VERSION + "\n", encoding="ascii")


def main():
    if (TARGET / "VERSION").is_file() and (TARGET / "VERSION").read_text(encoding="ascii").strip() == VERSION:
        print(f"Alice Homebrew {VERSION} already prepared")
        return
    unpack(download())
    print(f"Verified Alice Homebrew {VERSION} prepared in {TARGET}")


if __name__ == "__main__":
    main()
