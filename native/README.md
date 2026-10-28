# Station Mini driver helper

`station_driver.c` is LGPL-3.0-or-later and links statically to upstream libwdi 1.5.1 (LGPL-3.0-or-later). It exposes only `boot` and `adb`, with fixed speaker VID/PID values. It never replaces the composite parent or RNDIS network interface. For a non-present ADB function it stages the stock MI_02; after boot the application can repair the actual matching ADB interface.

Source: https://github.com/pbatard/libwdi/tree/v1.5.1

Build with MinGW-w64 and the Microsoft WDK 8 redistributable WinUSB components. The reproducible build script is `build-helper.sh`; paths to extracted upstream sources and WDK redistribution files are arguments. The application requests native Windows UAC before running this helper. Driver-signature enforcement is not disabled, and no stealth certificate option is used.

On Debian/Ubuntu, build dependencies are `gcc-mingw-w64-x86-64 binutils-mingw-w64-x86-64 autoconf automake libtool make dos2unix msitools python3`. Full upstream source is included in `upstream/libwdi-1.5.1.tar.gz` (SHA-256 `a695e93db0977dfdc5c6a99a4ea91b22f9027547d0177b2a0f3075078643c929`). The original archive is also available from [upstream v1.5.1](https://github.com/pbatard/libwdi/archive/refs/tags/v1.5.1.tar.gz).

Obtain the original [Microsoft WDK 8 redistributable](https://go.microsoft.com/fwlink/p/?LinkID=253170) (MSI SHA-256 `29314207814ce9d5d73695f7e9239539cf37c79e750b9d5ea5a5ef5487a583d6`). Extract using `msiextract -C wdk wdfcoinstaller.msi`. Pass the extracted Windows Kits **8.0 root**, containing `redist/wdf/x64/`, as the second argument:

```bash
tar xf upstream/libwdi-1.5.1.tar.gz
bash build-helper.sh "$PWD/libwdi-1.5.1" "$PWD/wdk/Program Files/Windows Kits/8.0"
```

This creates `../resources/station-driver-helper.exe` and its SHA-256 JSON manifest. The recipe overrides upstream's example Windows paths, includes only WinUSB x64, and sets the WDK directory macros omitted by upstream cross-compilation. You may rebuild with a modified libwdi and replace the helper plus its manifest; the application accepts that rebuilt pair. Building the Windows application then embeds those resources. Output bytes may differ with compiler versions and PE timestamps.

Microsoft redistributable terms and libwdi LGPL/GPL notices are in `../licenses/libwdi/`. The helper and the complete libwdi source/build recipe accompany binary releases. For hardware-validation scope see ../docs/VALIDATION.md.

The separate libusb 1.0.26 LGPL source used by the Python USB backend is also provided under upstream/; it is not part of the libwdi helper. Component download origins/checksums are in upstream/SOURCES.json.

The optional fourth argument is a UTF-16 Windows path for a UTF-8 diagnostic log. Manifest format 2 advertises `diagnostic_log: true`; the application creates a unique per-attempt file, displays the last 45 lines and retains the full native log on disk. Only fixed boot/ADB target IDs are accepted.
