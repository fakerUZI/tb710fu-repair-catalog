#!/bin/sh
set -eu
cd "$(dirname "$0")"
uuid=tb-external-desktop-input@tb710fu
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
gnome-extensions pack --out-dir="$stage" --extra-source=desktop-keys.js --extra-source=touchpad.js --extra-source=primary-monitor.js .
gnome-extensions install --force "$stage/$uuid.shell-extension.zip"
if gnome-extensions info "$uuid" >/dev/null 2>&1; then
    gnome-extensions enable "$uuid"
else
    # A running Shell discovers a newly installed UUID on its next login.
    python3 - "$uuid" <<'PY'
import sys
from gi.repository import Gio

uuid = sys.argv[1]
settings = Gio.Settings.new('org.gnome.shell')
settings.delay()
for key, enabled in [('enabled-extensions', True), ('disabled-extensions', False)]:
    values = settings.get_strv(key)
    if enabled and uuid not in values:
        values.append(uuid)
    elif not enabled and uuid in values:
        values.remove(uuid)
    else:
        continue
    if not settings.set_strv(key, values):
        sys.exit(f'无法更新 {key}')
settings.apply()
Gio.Settings.sync()
PY
fi
