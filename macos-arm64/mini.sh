#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
umask 077
project=$(cd "$(dirname "$0")" && pwd)
session=${MINI_SESSION:-"$HOME/Library/Application Support/Station Mini macOS"}
builder_commit=e5059ac0f08a3d5298a127533e91b5a1a4e427b2
container=mini1-macos-builder:0.3.15
adb_port=${MINI_ADB_PORT:-5038}

usage() {
    cat <<'HELP'
Station Mini 1 (YNDX-0004) • macOS ARM64

./mini.sh probe              Проверить USB-загрузчик
./mini.sh backup             Считать system и сохранить SHA-256
./mini.sh build              Собрать образ из сохранённого бэкапа
./mini.sh flash              Записать образ, сверить SHA-256, проверить ADB root
./mini.sh root               Выполнить backup → build → flash
./mini.sh restore            Вернуть system из бэкапа с проверкой SHA-256
./mini.sh homebrew           Установить Alice Homebrew
./mini.sh airplay            Установить Station AirPlay
./mini.sh spotify            Установить horohi/soloist, ввести ключ Spotify
./mini.sh status             Проверить колонку, службы и узнать её IP
./mini.sh all                Выполнить root → homebrew → airplay → spotify

MINI_SESSION задаёт папку бэкапа и ключа для одной колонки.
MINI_ADB_PORT задаёт порт локального сервера ADB (по умолчанию 5038).
HELP
}

fail() { printf '%s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || fail "Нужен $1. См. README."; }
hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }

usb_tool() {
    need clang
    if [[ ! -x "$session/mini-usb" || "$project/src/mini-usb.c" -nt "$session/mini-usb" ]]; then
        clang -arch arm64 -O2 -Wall -Wextra -Werror -Wno-deprecated-declarations \
            -framework IOKit -framework CoreFoundation "$project/src/mini-usb.c" -o "$session/mini-usb"
    fi
    caffeinate -i -s "$session/mini-usb" "$@"
}

confirm_model() {
    local label
    read -r -p 'Сверьте наклейку колонки и введите YNDX-0004: ' label
    [[ "$label" = YNDX-0004 ]] || fail 'Модель не подтверждена.'
}

backup() {
    [[ ! -e "$session/system-backup.bin" ]] || fail 'Бэкап уже есть. Для новой колонки задайте другую MINI_SESSION.'
    local scratch
    scratch=$(mktemp -d "$session/backup.XXXXXX")
    (
        trap 'rm -rf "$scratch"' EXIT
        usb_tool backup "$scratch/system.bin"
        hash_file "$scratch/system.bin" > "$scratch/sha256"
        mv "$scratch/sha256" "$session/system-backup.sha256"
        mv "$scratch/system.bin" "$session/system-backup.bin"
    )
    printf 'Бэкап сохранён в %s\n' "$session/system-backup.bin"
}

check_backup() {
    [[ -s "$session/system-backup.sha256" && -f "$session/system-backup.bin" ]] || fail 'Сначала выполните backup.'
    [[ $(hash_file "$session/system-backup.bin") = "$(cat "$session/system-backup.sha256")" ]] || fail 'SHA-256 бэкапа не совпал.'
}

prepare_upstream() {
    need git
    if [[ ! -d "$session/upstream/.git" ]]; then
        local scratch
        scratch=$(mktemp -d "$session/upstream.XXXXXX")
        if git clone --depth 1 --branch v0.3.15 https://github.com/hertebeznat-cell/alice-station-1-mini-root.git "$scratch" &&
            [[ $(git -C "$scratch" rev-parse HEAD) = "$builder_commit" ]]; then
            mv "$scratch" "$session/upstream"
        else
            rm -rf "$scratch"
            fail 'Не удалось получить проверенную версию Station Mini Root.'
        fi
    fi
    [[ $(git -C "$session/upstream" rev-parse HEAD) = "$builder_commit" &&
       -z $(git -C "$session/upstream" status --porcelain --untracked-files=no) ]] || fail 'Локальный исходник сборщика изменился.'
}

build() {
    check_backup
    need adb
    need docker
    docker info >/dev/null 2>&1 || fail 'Запустите Docker Desktop и повторите build.'
    [[ ! -e "$session/image" ]] || fail 'Образ уже собран. Для повторной сборки переместите папку image.'
    prepare_upstream
    if [[ ! -f "$session/adbkey" && ! -e "$session/adbkey.pub" ]]; then
        adb keygen "$session/adbkey"
    fi
    [[ -s "$session/adbkey" && -s "$session/adbkey.pub" ]] || fail 'Нужна полная пара ADB-ключей.'
    docker build --platform linux/arm64 -t "$container" - < "$project/Dockerfile"
    local stage
    stage=$(mktemp -d "$session/image.XXXXXX")
    (
        trap 'rm -rf "$stage"' EXIT
        docker run --rm --network none \
            --mount "type=bind,src=$session/upstream,dst=/src,readonly" \
            --mount "type=bind,src=$session/system-backup.bin,dst=/backup.bin,readonly" \
            --mount "type=bind,src=$session/adbkey.pub,dst=/adbkey.pub,readonly" \
            --mount "type=bind,src=$stage,dst=/out" \
            -e STATION_ROOT_PYTHON=/opt/venv/bin/python "$container" \
            bash /src/scripts/build_image.sh /backup.bin /adbkey.pub /out/system-root.ubi
        mv "$stage" "$session/image"
    )
}

# Keep the project ADB server separate. Another ADB server can hold the USB interface.
adb_usb() { adb -P "$adb_port" -d "$@"; }

# The Mini's legacy adbd reports zero even when the remote command fails.
station_shell() {
    local output last
    output=$(adb_usb shell "(
$1
)
mini_status=\$?
printf '\n__MINI_EXIT__:%s\n' "\$mini_status"" | tr -d '\r') || return
    last=${output##*$'\n'}
    if [[ "$last" != __MINI_EXIT__:0 ]]; then
        [[ -z "$output" ]] || printf '%s\n' "$output" >&2
        return 1
    fi
    output=${output%$'\n'*}
    [[ -z "$output" ]] || printf '%s\n' "$output"
}

adb_start() {
    need adb
    [[ -s "$session/adbkey" ]] || fail 'ADB-ключ отсутствует в MINI_SESSION.'
    export ADB_VENDOR_KEYS="$session/adbkey"
    adb -P "$adb_port" start-server
}

check_station() {
    adb_start
    station_shell 'test "$(id -u)" = 0 && test -s /etc/station-mini-root.json && test "$(uname -m)" = aarch64' ||
        fail 'Нужна одна Мини 1 с USB ADB root. Разрешите USB-аксессуар macOS и проверьте ADB. Если другой ADB-сервер занял USB, завершите его и переподключите колонку.'
}

image_metadata() {
    need python3
    python3 - "$session" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

session = Path(sys.argv[1])
report = json.loads((session / "image/system-root.json").read_text())
source = (session / "system-backup.sha256").read_text().strip()
key = (session / "adbkey.pub").read_text().strip() + "\n"
if (
    report.get("model") != "YNDX-0004"
    or report.get("partition") != "system"
    or report.get("builder") != "0.3.15"
    or report.get("source_sha256") != source
    or report.get("public_key_sha256") != hashlib.sha256(key.encode()).hexdigest()
):
    raise SystemExit("Отчёт сборки не совпадает с бэкапом, моделью или ADB-ключом.")
print(report["image_sha256"])
PY
}

flash_image() {
    check_backup
    local digest original
    original=$(cat "$session/system-backup.sha256")
    if [[ "$1" = restore ]]; then
        local answer snapshot
        read -r -p 'Для восстановления той же колонки из её бэкапа введите RESTORE: ' answer
        [[ "$answer" = RESTORE ]] || fail 'Восстановление отменено.'
        snapshot=$(mktemp -d "$session/before-restore.XXXXXX")
        printf 'Копия текущего system будет сохранена в %s\n' "$snapshot"
        usb_tool restore "$session/system-backup.bin" "$snapshot/system.bin" "$original"
        return
    fi
    digest=$(image_metadata)
    adb_start
    usb_tool flash "$session/image/system-root.ubi" "$original" "$digest"
    printf '%s\n' 'Если macOS спросит о новом USB-аксессуаре после перезагрузки, нажмите «Разрешить».'
    local attempt
    for ((attempt=0; attempt<45; attempt++)); do
        if station_shell 'test "$(id -u)" = 0 && test -s /etc/station-mini-root.json && id' 2>/dev/null; then
            return
        fi
        sleep 2
    done
    fail 'Раздел записан и SHA-256 совпал. USB ADB root пока не подтвердился. Проверьте доступ через status.'
}

fetch() {
    local url=$1 digest=$2 destination=$3
    if [[ ! -f "$destination" ]] || [[ $(hash_file "$destination") != "$digest" ]]; then
        curl --proto '=https' -fLSs --retry 2 -o "$destination.part" "$url"
        [[ $(hash_file "$destination.part") = "$digest" ]] || fail "Неверный SHA-256 файла $destination."
        mv -f "$destination.part" "$destination"
    fi
}

homebrew() {
    check_station
    need python3
    local archive="$session/AliceHomebrew-6.0.1.zip" stage
    fetch https://github.com/hertebeznat-cell/alice-homebrew/releases/download/v6.0.1/AliceHomebrew-6.0.1.zip \
        dc53b0a7701545f987a9a2ccbb8d7fc1eb6e054d5313669df7169bd3a45fb338 "$archive"
    stage=$(mktemp -d "$session/homebrew.XXXXXX")
    (
        trap 'rm -rf "$stage"' EXIT
        unzip -q "$archive" bin/alice-homebrew-linux-arm64 scripts/install-speaker.py \
            scripts/start-speaker.sh scripts/S98alice-homebrew -d "$stage"
        python3 "$stage/scripts/install-speaker.py" --adb "$(command -v adb)" --adb-port "$adb_port" \
            --key "$session/adbkey" --binary "$stage/bin/alice-homebrew-linux-arm64" --install --autostart
    )
}

airplay() {
    check_station
    local archive="$session/station-airplay-0.2.3-linux-arm64.tar.gz" stage remote
    fetch https://github.com/skrashevich/station-airplay/releases/download/v0.2.3/station-airplay-0.2.3-linux-arm64.tar.gz \
        54e3ec2363da9740305c220718a439bb173b1a998483e430da101011e918a4cf "$archive"
    stage=$(mktemp -d "$session/airplay.XXXXXX")
    remote=$(station_shell 'mktemp -d /tmp/mini-airplay.XXXXXX' | tr -d '\r')
    [[ "$remote" =~ ^/tmp/mini-airplay\.[A-Za-z0-9]+$ ]] || fail 'Не создана временная папка на колонке.'
    (
        trap 'station_shell "rm -rf $remote" >/dev/null 2>&1 || true; rm -rf "$stage"' EXIT
        tar -xf "$archive" -C "$stage" station-airplay scripts/install.sh scripts/service.sh
        adb_usb push "$stage/." "$remote/"
        cat > "$stage/install-remote.sh" <<'REMOTE'
set -e
chmod 700 station-airplay
mount -o remount,rw /
trap 'mount -o remount,ro /' EXIT
sh scripts/install.sh --name 'Alice Mini' --interface wlan0 --autostart
sync
/data/opt/station-airplay/service status
REMOTE
        adb_usb push "$stage/install-remote.sh" "$remote/install-remote.sh"
        station_shell "cd '$remote' && sh install-remote.sh"
    )
}

spotify() {
    check_station
    local archive="$session/horohi-soloist-1.3.12-yandexmini.yampkg" remote key
    local attempt
    for ((attempt=0; attempt<20; attempt++)); do
        if station_shell 'test -x /data/yam/bin/yam-run' 2>/dev/null; then break; fi
        if ((attempt == 0)); then printf '%s\n' 'Жду первичной настройки YAM в Alice Homebrew.'; fi
        sleep 3
    done
    if ! station_shell 'test -x /data/yam/bin/yam-run'; then
        fail 'На колонке нужен YAM v2. Установите Alice Homebrew, дождитесь инициализации YAM и повторите spotify. См. README.'
    fi
    fetch https://github.com/Horohi/soloist-station-lite/releases/download/v1.3.12/horohi-soloist-1.3.12-yandexmini.yampkg \
        1d939e35f26ef75e88105824371c7d756723c4d2d7f486ff56dd353770d8a482 "$archive"
    printf '%s\n' 'Ключ Spotify Soloist создаётся на https://developer.spotify.com/dashboard/soloist'
    read -r -s -p 'Ключ Spotify Soloist (Enter, чтобы оставить ранее настроенный): ' key
    printf '\n'
    if [[ -n "$key" ]]; then
        [[ "$key" =~ ^[A-Za-z0-9._=+/-]+$ ]] || fail 'Некорректные символы в ключе.'
    else
        station_shell 'test -s /data/yam/etc/horohi.soloist/config.env && grep -q "^API_KEY=\"[^\"]" /data/yam/etc/horohi.soloist/config.env' ||
            fail 'Ключ ещё не настроен.'
    fi
    if ! station_shell 'test -x /data/yam/share/horohi.soloist/bin/soloist ||
        [ "$(df -k /data | awk "END {print \$4}")" -ge 51200 ]'; then
        fail 'Для первой установки Spotify освободите 50 МиБ на /data. См. README.'
    fi
    remote=$(station_shell 'mktemp -d /tmp/mini-spotify.XXXXXX' | tr -d '\r')
    [[ "$remote" =~ ^/tmp/mini-spotify\.[A-Za-z0-9]+$ ]] || fail 'Не создана временная папка на колонке.'
    (
        trap 'station_shell "rm -rf $remote" >/dev/null 2>&1 || true' EXIT
        adb_usb push "$archive" "$remote/soloist.yampkg"
        if [[ -n "$key" ]]; then
            local config_tmp
            config_tmp=$(mktemp "$session/spotify-config.XXXXXX")
            trap 'rm -f "$config_tmp"; station_shell "rm -rf $remote" >/dev/null 2>&1 || true' EXIT
            printf 'API_KEY="%s"\nDEVICE_NAME="Alice Mini Spotify"\n' "$key" > "$config_tmp"
            adb_usb push "$config_tmp" "$remote/config.env"
            rm -f "$config_tmp"
        fi
        adb_usb push "$project/src/install-spotify.sh" "$remote/install-spotify.sh"
        station_shell "cd '$remote' && sh install-spotify.sh"
    )
    unset key
    printf '%s\n' 'Выберите Alice Mini Spotify в Spotify Connect. Громкость регулируется из Spotify.'
}

show_status() {
    check_station
    station_shell '
set -e
id
ip -4 addr show wlan0
if [ -x /data/opt/station-airplay/service ]; then
    /data/opt/station-airplay/service status || true
fi
if [ -x /data/yam/bin/yam-run ]; then
    /data/yam/bin/yam-run list
fi
p=/data/yam/share/horohi.soloist
if [ -x "$p/bin/soloist" ]; then
    "$p/lib64/ld-linux-aarch64.so.1" --library-path "$p/lib64" "$p/bin/soloist" ctl -w 127.0.0.1:9090 now || true
fi
    '
}

action=${1:-help}
case "$action" in help|-h|--help) usage; exit 0;; esac
[[ $# -eq 1 ]] || { usage >&2; exit 2; }
[[ $(uname -s) = Darwin && $(uname -m) = arm64 ]] || fail 'Нужен Mac с Apple Silicon и нативный Терминал.'
mkdir -p "$session"
session=$(cd "$session" && pwd)
mkdir "$session/.lock" 2>/dev/null || fail 'Сессия уже занята. После аварийного завершения удалите пустую папку .lock вручную.'
trap 'rmdir "$session/.lock"' EXIT

case "$action" in
    probe) usb_tool probe ;;
    backup) confirm_model; backup ;;
    build) build ;;
    flash|restore) confirm_model; flash_image "$action" ;;
    root|all)
        for dependency in clang git adb docker python3; do need "$dependency"; done
        docker info >/dev/null 2>&1 || fail 'Запустите Docker Desktop и повторите.'
        confirm_model
        if [[ -f "$session/system-backup.bin" ]]; then check_backup; else backup; fi
        if [[ ! -d "$session/image" ]]; then build; fi
        flash_image flash
        if [[ "$action" = all ]]; then homebrew; airplay; spotify; fi
        ;;
    homebrew) homebrew ;;
    airplay) airplay ;;
    spotify) spotify ;;
    status) show_status ;;
    *) usage >&2; exit 2 ;;
esac
