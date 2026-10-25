#!/usr/bin/env bash
set -euo pipefail
# Usage: build-helper.sh /linux/path/to/libwdi-source /linux/path/to/wdk-redist
SOURCE=$(realpath "$1")
WDK=$(realpath "$2")
PROJECT=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$SOURCE"
ln -sfn "$WDK" "$SOURCE/wdk-files"
./autogen.sh --host=x86_64-w64-mingw32 --disable-shared --enable-static --disable-debug \
    --disable-toggable-debug --disable-examples-build --with-libusb0= --with-libusbk= \
    --disable-32bit --enable-64bit --with-wdkdir="$SOURCE/wdk-files"
# Upstream skips WDK directory detection while cross-compiling. The WDK 8
# redistributable uses these two directories; retain the original DLLs.
test -f "$WDK/redist/wdf/x64/WdfCoInstaller01011.dll"
test -f "$WDK/redist/wdf/x64/winusbcoinstaller2.dll"
make config.h
printf '\n#define X64_DIR "x64"\n#define COINSTALLER_DIR "wdf"\n' >> config.h
make -C libwdi clean
make -j2
mkdir -p "$PROJECT/resources"
x86_64-w64-mingw32-gcc -municode -O2 -static -I"$SOURCE/libwdi" \
    "$PROJECT/native/station_driver.c" "$SOURCE/libwdi/.libs/libwdi.a" \
    -lsetupapi -lcfgmgr32 -lwintrust -lcrypt32 -lole32 -lshell32 -lshlwapi -luser32 -lversion -ladvapi32 \
    -o "$PROJECT/resources/station-driver-helper.exe"
python3 - "$PROJECT/resources/station-driver-helper.exe" <<'PY'
from pathlib import Path
import hashlib
import json
import sys
binary = Path(sys.argv[1])
binary.with_suffix('.json').write_text(json.dumps({
    'format': 1, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'libwdi': '1.5.1', 'target': 'windows-x64'
}, indent=2) + '\n', encoding='utf-8')
PY
