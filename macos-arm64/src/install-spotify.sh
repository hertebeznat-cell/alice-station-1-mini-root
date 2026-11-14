#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu
umask 077
yam=/data/yam/bin/yam-run
config=/data/yam/etc/horohi.soloist/config.env
hook=/etc/init.d/S98soloist
package=/data/yam/share/horohi.soloist

# The package hook disables the Spotiya boot script. Stop its current process
# before both receivers can compete for the same sound device.
if [ -x /etc/init.d/S99spotify-connect ]; then
    /etc/init.d/S99spotify-connect stop
fi
"$yam" install -y ./soloist.yampkg
"$hook" stop
if [ -s config.env ]; then
    install_dir=${config%/*}
    mkdir -p "$install_dir"
    cp config.env "$config.new"
    chmod 600 "$config.new"
    mv "$config.new" "$config"
fi

service="$package/scripts/soloist-service.sh"
init="$package/scripts/yam-init.sh"
grep -q 'APULSE_EXTERNAL_VOLUME=[01]' "$service"
sed -i 's/export APULSE_EXTERNAL_VOLUME=1/export APULSE_EXTERNAL_VOLUME=0/' "$service"
# Use Spotify's digital volume. The optional bridge otherwise syncs that value
# back to Quasar. Its voice and LED features are disabled with it.
sed -i 's@if \[ -x "\$PKG_SHARE/bin/soloist-bridge" \]; then@if false; then@' "$init"
sed -i 's/ \&\& pid_alive "\$DATA_ROOT\/bridge.pid" soloist-bridge//g' "$init"
sh -n "$service"
sh -n "$init"
test -x "$hook"
"$hook" start
sleep 2
pid=$(cat /data/yam/var/horohi.soloist/soloist.pid)
kill -0 "$pid"
