"""Restore launcher links created by version 1.0.2 during rollback."""
import os
from pathlib import Path
import subprocess
import sys

HOME = Path.home()
UI = HOME / ".local/libexec/tb710fu-display-settings/gnome-control-center"
BACKUP = UI.with_name(UI.name + ".before-face-password")
TARGET = Path("/usr/libexec/tb710fu-face-password/gnome-control-center")
LAUNCHER = HOME / ".local/bin/gnome-control-center"
BUS = HOME / ".local/share/dbus-1/services/org.gnome.Settings.service"
OWN = HOME / ".local/state/tb710fu-face-password"


def points(path, target):
    return path.is_symlink() and path.readlink() == target


def refresh():
    subprocess.run(["/usr/bin/gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                    "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ReloadConfig"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)


def rollback():
    if points(UI, TARGET):
        if BACKUP.exists():
            os.replace(BACKUP, UI)
        else:
            UI.unlink()
    for path in (LAUNCHER, BUS):
        dest = OWN / path.name
        if points(path, dest):
            path.unlink()
            dest.unlink(missing_ok=True)
    if OWN.is_dir() and not any(OWN.iterdir()):
        OWN.rmdir()
    refresh()


if __name__ == "__main__":
    if os.geteuid() == 0:
        sys.exit("设置入口必须以桌面用户身份操作。")
    try:
        if sys.argv[1] == "rollback":
            rollback()
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
