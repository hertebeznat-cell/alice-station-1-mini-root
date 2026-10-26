# Station Mini Root 0.3.12

[Русский](README.md) · [Windows / Linux downloads](https://github.com/hertebeznat-cell/alice-station-1-mini-root/releases/tag/v0.3.12) · [Method](docs/METHOD.md) · [Validation](docs/VALIDATION.md)

USB ADB root application for **Yandex Station Mini 1, YNDX-0004 / Amlogic A113X**. English/Russian UI; Windows x64 and Linux x86_64. Windows uses a compatible Debian/Ubuntu WSL builder and can install official Debian when needed.

## Use

The Windows executable includes libusb and the WinUSB helper; end users do not compile them. If 0.3.8 reports a missing libusb, close it, download 0.3.12 and retry dependency setup. Existing WSL, ADB, workspaces and backups are preserved.

1. Download and extract your platform archive. Run StationMiniRoot.exe on Windows, or `chmod +x StationMiniRoot && ./StationMiniRoot` on Linux.
2. Click **Install all dependencies**. ADB is detected or downloaded from Google, the Linux builder is prepared, and the matching USB driver/udev rules are configured. Accept native Windows UAC/driver trust prompts yourself.
3. Connect Mini 1 with a data-capable USB cable when prompted. The owner's confirmed method does not require holding speaker buttons: the program captures the TPL loader and keeps it active through USB polling. Root/restore stay locked until readiness succeeds.
4. Click **Install root** and confirm YNDX-0004. The program saves the complete 80 MiB system, patches/builds from that backup, writes only system, verifies a full read-back and SHA-256, reboots and verifies ADB uid=0.
5. Homebrew 0.5.3 and its boot hook install automatically after root verification. Open the speaker IP on port 8088 without a login/token. Homebrew checks stable manager updates every five hours. AirPlay and other plugins are separate installations.

Keep USB and power connected during write/read-back. Other speaker models are unsupported. The Linux release targets Ubuntu 24.04 / glibc 2.39; build on the target system when using an older distribution.

Only recognized stock OTA paths are disabled. Stock Alice, unrelated applications and other firmware partitions are preserved. A failed Homebrew deployment can resume without reflashing. This uses accessible TPL/U-Boot and does not establish an unpatchable BootROM exploit. See docs/METHOD.md.

## Backup / restore

Workspace: `%LOCALAPPDATA%\StationMiniRoot\work` on Windows, `${XDG_DATA_HOME:-~/.local/share}/StationMiniRoot/work` on Linux. Existing saved/legacy workspaces are reused. Preserve this folder and never publish it: it contains your private ADB key, firmware backup and session record.

Restore backup returns the exact pre-change system and its OTA behavior, with a backup of the current system and a full read-back check. A previously modified source restores that previous modification, not a factory image. Homebrew data resides in /data; restoring system is not a promise to remove it.

## Build

```bash
python -m pip install -r requirements.txt pyinstaller==6.16.0
python scripts/prepare-homebrew.py
python -m unittest discover -s tests -v
python scripts/build-app.py
```

Build natively for each platform. On Windows first build the LGPL driver helper as described in native/README.md, using the separately obtained Microsoft WDK redistributables. The helper targets only the speaker boot/ADB functions, preserves composite/RNDIS interfaces and does not disable driver-signature enforcement.

The Homebrew preparer downloads the pinned official 0.4.1 ARM64 binary and checks its SHA-256/size. Homebrew sources are included under alice-homebrew/ and keep their separate licenses. You can build that service with Go 1.26.5 instead; the Russian README includes the cross-build command.

Windows source launch: create a venv, install requirements.txt, then run station_mini_root.py. Debian/Ubuntu source setup: `bash scripts/setup-linux.sh`, followed by `bash start-linux.sh`.

## License

Original application code is source-available under the proprietary LICENSE: personal non-commercial use of official binaries is permitted; reuse/redistribution of original source and publication of modified versions require prior written permission. This is not an open-source license. GitHub's own viewing/forking terms still apply.

The MIT transport, LGPL helper/libusb, MIT Homebrew and third-party components retain their licenses. See THIRD_PARTY.md, licenses/ and native/upstream/. No personal keys, device dumps, accounts or proprietary firmware images are distributed.

## Existing noupdate patch

0.3.12 accepts the recognized older noupdate patch only when the exact inert apply script and the normal Mini 1 main task are present. Other startup tasks are preserved and known OTA entry points are blocked. Arbitrary absence of updatesd still stops the build. If 0.3.9 stopped at this check before flashing, close it and retry Install root using 0.3.12 while keeping the same workspace, keys, backups and WSL.

## Homebrew-only maintenance

For an already rooted speaker, connect normal ADB on the original PC and choose Update Homebrew only (ADB root). The original authorized key is required. No TPL capture or system image flashing occurs. This also bootstraps Homebrew 0.5.3 when the older manager cannot update because its firmware has no usable CA store.

## Verified updater_switcher firmware

0.3.12 recognizes the confirmed Mini 1 switcher by exact startup arguments, the normal platformd task and the switcher SHA-256. Only OTA startup is removed; other launcher tasks and metadata are preserved. Unknown variants still stop before flashing. If an earlier build failed at the missing-updatesd check, close it and retry Install root with 0.3.12 while retaining the workspace, authorized key, backups and WSL.
