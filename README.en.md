# Station Mini Root

[Русский](README.md) · [Download](https://github.com/hertebeznat-cell/alice-station-1-mini-root/releases/latest) · [How the method works](docs/METHOD.md) · [Supported firmware](docs/FIRMWARE.md)

Gets **ADB root on Yandex Station Mini, 1st generation** (YNDX-0004, Amlogic A113X) and installs [Alice Homebrew](https://github.com/hertebeznat-cell/alice-homebrew), a web panel with plugins. Windows x64 and Linux x86_64, English and Russian UI.

Other speaker models are not supported, and the app checks for that itself.

## How to use

1. Download the archive for your system and extract it.
   Windows: `StationMiniRoot.exe`. Linux: `chmod +x StationMiniRoot && ./StationMiniRoot`.
2. **Install all dependencies.** The app finds or downloads ADB; on Windows it prepares WSL with the image builder, on Linux the tools and udev rules. Confirm UAC and driver-trust prompts in the system windows.
3. Connect the Mini 1 with a data USB cable when asked. No buttons need to be held: the app catches the bootloader and keeps it.
4. **Install root.** Everything else is automatic: backup → image build → write → verify → reboot → root check → Alice Homebrew install.
5. Open `http://SPEAKER-IP:8088` — the Homebrew panel. No login.

Keep power and the cable connected while writing.

## What changes on the speaker

```text
backup system (80 MiB) → patch ADB and OTA → build the image from your backup
→ write system → full read-back and SHA-256 check
→ boot → check uid=0 over ADB → install Alice Homebrew into /data
```

- **Only the system partition** is rewritten. Bootloader, boot, nv and user data are untouched.
- Stock Alice and its services keep running.
- Yandex firmware updates are disabled, otherwise an update writes a factory system back and root is lost. Only known update mechanisms are disabled; an unknown firmware layout stops the build **before writing** ([details](docs/FIRMWARE.md)).
- If Homebrew fails to install, running Install root again finishes it without reflashing.

## Backup and restore

Workspace:

- Windows: `%LOCALAPPDATA%\StationMiniRoot\work`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/StationMiniRoot/work`

It holds your private ADB key, the original system backup and `session.json`. **Keep it and never publish it.**

Restore backup writes the original system back (with a read-back check) and returns the factory update behavior. Homebrew lives in `/data`; restoring system does not remove it.

## Update Homebrew without flashing

Connect the already rooted speaker over ADB to the same computer and choose "Update Homebrew only (ADB root)". The original workspace with the ADB key is required. After that Homebrew updates itself from its own panel.

## Build from source

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt pyinstaller==6.16.0
.venv/bin/python scripts/prepare-homebrew.py   # downloads the pinned Homebrew release and checks SHA-256
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python scripts/build-app.py
```

On Windows use `.venv\Scripts\python`. For the Windows build, first build the WinUSB helper: [native/README.md](native/README.md). Running from source: `python station_mini_root.py` (Linux: `bash scripts/setup-linux.sh`, then `bash start-linux.sh`).

Building an image by hand from **your own** backup:

```bash
adb keygen /path/to/adbkey
bash scripts/build_image.sh system-backup.bin /path/to/adbkey.pub output.ubi
```

### Code layout

| Module | Purpose |
| --- | --- |
| `station_root/mini1.py` | Everything known about the Mini 1: USB IDs, partition size, UBI geometry, firmware paths |
| `station_root/data/known-firmware.json` | Allowlist of verified updater binaries |
| `station_root/usb_transport.py` | Reading and writing the system partition through the bootloader |
| `station_root/image_builder.py` | Rootfs patch and UBI build (Linux/WSL, under fakeroot) |
| `station_root/adb.py` | Root check over ADB without touching your ADB server |
| `station_root/workflow.py` | Step order: backup → image → write → verify → Homebrew |
| `station_root/gui.py` | Application window |

## Licenses

The original code is source-available under [proprietary terms](LICENSE): the official program may be used personally and non-commercially; redistribution and modified versions require the author's written permission. This is not open source.

The USB transport (MIT, based on pyamlboot), WinUSB helper and libusb (LGPL), Alice Homebrew (MIT) and other components keep their own licenses: [THIRD_PARTY.md](THIRD_PARTY.md), `licenses/`, `native/upstream/`.

Yandex firmware, built images, keys and dumps are not part of the repository or releases.
