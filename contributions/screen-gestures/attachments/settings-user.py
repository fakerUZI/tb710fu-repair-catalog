#!/usr/bin/python3
"""Install and restore the Settings launcher as the desktop user."""
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import os

HOME = Path.home()
BASE = HOME / ".local/state/tb710fu-screen-gestures"
LAUNCHER = HOME / ".local/bin/gnome-control-center"
BINARY = HOME / ".local/libexec/tb710fu-display-settings/gnome-control-center"
DBUS_SERVICE = HOME / ".local/share/dbus-1/services/org.gnome.Settings.service"
MARKER = b"# TB710FU Display Settings\n"
VERSION = b"1:50.3-0ubuntu0.2"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tb710fu-settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def current(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Unexpected Settings path: {path}")
    return path.read_bytes() if path.exists() else None


def refresh_bus():
    if os.getenv("DBUS_SESSION_BUS_ADDRESS"):
        try:
            subprocess.run(("/usr/bin/gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                            "--object-path", "/org/freedesktop/DBus",
                            "--method", "org.freedesktop.DBus.ReloadConfig"),
                           capture_output=True, check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def payload(binary):
    launcher = b"""#!/bin/sh
# TB710FU Display Settings
binary="$HOME/.local/libexec/tb710fu-display-settings/gnome-control-center"
if [ -x "$binary" ] &&
   [ "$(dpkg-query -W -f='${Version}' gnome-control-center 2>/dev/null)" = '1:50.3-0ubuntu0.2' ] &&
   [ "$(dpkg-query -W -f='${Version}' gnome-control-center-data 2>/dev/null)" = '1:50.3-0ubuntu0.2' ]; then
    exec "$binary" "$@"
fi
exec /usr/bin/gnome-control-center "$@"
"""
    launcher_arg = str(LAUNCHER).replace("\\", "\\\\").replace('"', '\\"')
    if "\n" in launcher_arg or "\r" in launcher_arg:
        raise RuntimeError("Invalid home path for Settings activation")
    service = MARKER + ("[D-BUS Service]\nName=org.gnome.Settings\n"
                        f'Exec="{launcher_arg}" --gapplication-service\n').encode()
    return {BINARY: (binary, 0o755), LAUNCHER: (launcher, 0o755), DBUS_SERVICE: (service, 0o644)}


def rollback():
    if not (BASE / "state.json").is_file():
        return
    state = json.loads((BASE / "state.json").read_text())
    for path in (BINARY, LAUNCHER, DBUS_SERVICE):
        record = state[str(path)]
        actual = current(path)
        if actual is not None and digest(actual) not in (record["installed"], record.get("original")):
            raise RuntimeError(f"Settings file changed after installation: {path}")
        if "backup" in record and digest((BASE / record["backup"]).read_bytes()) != record["original"]:
            raise RuntimeError(f"Settings backup changed: {path}")

    for path in (BINARY, LAUNCHER, DBUS_SERVICE):
        record = state[str(path)]
        if "backup" in record:
            put(path, (BASE / record["backup"]).read_bytes(), record["mode"])
        else:
            path.unlink(missing_ok=True)
    (BASE / "state.json").unlink()
    for record in state.values():
        if "backup" in record:
            (BASE / record["backup"]).unlink(missing_ok=True)
    BASE.rmdir()
    refresh_bus()


def apply(binary_path):
    for package in ("gnome-control-center", "gnome-control-center-data"):
        version = subprocess.check_output(("/usr/bin/dpkg-query", "-W", "-f=${Version}", package),
                                          timeout=5).strip()
        if version != VERSION:
            raise RuntimeError(f"Unsupported Settings version: {package}")
    files = payload(binary_path.read_bytes())
    old = {path: current(path) for path in files}
    if all(old[path] == data for path, (data, _mode) in files.items()):
        return
    if any(old[path] is not None and MARKER not in old[path].splitlines(keepends=True)
           for path in (LAUNCHER, DBUS_SERVICE)):
        raise RuntimeError("Another Settings launcher or D-Bus override is installed.")
    if old[BINARY] is not None and old[LAUNCHER] is None and old[DBUS_SERVICE] is None:
        raise RuntimeError("Another Settings binary is installed.")
    BASE.mkdir(parents=True, mode=0o700)
    state = {}
    for index, (path, (data, _mode)) in enumerate(files.items()):
        state[str(path)] = {"installed": digest(data)}
        if old[path] is not None:
            backup = f"original-{index}"
            put(BASE / backup, old[path], 0o600)
            state[str(path)].update(backup=backup, original=digest(old[path]),
                                    mode=stat.S_IMODE(path.stat().st_mode))
    put(BASE / "state.json", json.dumps(state).encode(), 0o600)
    try:
        for path, (data, mode) in files.items():
            put(path, data, mode)
    except OSError:
        rollback()
        raise
    refresh_bus()
    print("installed")


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Settings integration requires the graphical user's privileges.")
    if sys.argv[1:] == ["rollback"]:
        rollback()
    elif len(sys.argv) == 3 and sys.argv[1] == "apply":
        apply(Path(sys.argv[2]))
    else:
        raise SystemExit("usage: settings-user.py apply BINARY | rollback")
