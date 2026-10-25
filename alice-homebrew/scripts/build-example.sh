#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
clang --target=wasm32 -nostdlib -O2 -fno-builtin \
  -Wl,--no-entry -Wl,--export=alice_tick -Wl,--export-memory \
  -Wl,--initial-memory=131072 -Wl,--max-memory=2097152 -Wl,--strip-all \
  "$ROOT/examples/ring-studio/plugin.c" -o "$ROOT/examples/ring-studio/plugin.wasm"
