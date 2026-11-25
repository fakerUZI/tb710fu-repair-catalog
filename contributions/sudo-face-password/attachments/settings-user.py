"""Connect the packaged Settings build to the graphical user's existing launcher."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOME = Path.home()
UI = HOME / ".local/libexec/tb710fu-display-settings/gnome-control-center"
BACKUP = UI.with_name(UI.name + ".before-face-password")
TARGET = Path("/usr/libexec/tb710fu-face-password/gnome-control-center")
LAUNCHER = HOME / ".local/bin/gnome-control-center"
BUS = HOME / ".local/share/dbus-1/services/org.gnome.Settings.service"
OWN = HOME / ".local/state/tb710fu-face-password"
BASE = "2d429272d4393674ffe13b2f6d42a65dfd2998c0b6bb871a2bef597acd69ef57"
MARKER = "# TB710FU Display Settings"
VERSION = "1:50.3-0ubuntu0.2"


def baseline(path):
    return path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == BASE


def points(path, target):
    return path.is_symlink() and path.readlink() == target


def check():
    if os.path.lexists(UI) and not points(UI, TARGET) and not baseline(UI):
        raise ValueError("当前定制设置版本不同，已保留原文件。")
    if os.path.lexists(BACKUP) and not baseline(BACKUP):
        raise ValueError("当前设置备份与已验证的版本不同。")
    for path in (LAUNCHER, BUS):
        if os.path.lexists(path) and not points(path, OWN / path.name):
            if path.is_symlink() or not path.is_file() or MARKER not in path.read_text().splitlines():
                raise ValueError(f"请先核对已有的设置启动入口：{path}")


def install_link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        link = Path(tmp) / "link"
        link.symlink_to(target)
        os.replace(link, path)


def refresh():
    subprocess.run(["/usr/bin/gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                    "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ReloadConfig"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)


def apply():
    if baseline(UI) and not BACKUP.exists():
        os.link(UI, BACKUP)
    if not points(UI, TARGET):
        install_link(UI, TARGET)
    executable = str(LAUNCHER).replace("\\", "\\\\").replace('"', '\\"')
    if "\n" in executable or "\r" in executable:
        raise ValueError("主目录路径含有换行符。")
    runner = f'''#!/bin/sh
{MARKER}
binary="$HOME/.local/libexec/tb710fu-display-settings/gnome-control-center"
if [ -x "$binary" ] &&
   [ "$(dpkg-query -W -f='${{Version}}' gnome-control-center)" = '{VERSION}' ] &&
   [ "$(dpkg-query -W -f='${{Version}}' gnome-control-center-data)" = '{VERSION}' ]; then
    exec "$binary" "$@"
fi
exec /usr/bin/gnome-control-center "$@"
'''
    service = f'{MARKER}\n[D-BUS Service]\nName=org.gnome.Settings\nExec="{executable}" --gapplication-service\n'
    for path, data, mode in ((LAUNCHER, runner, 0o755), (BUS, service, 0o644)):
        if path.exists():
            continue
        OWN.mkdir(parents=True, exist_ok=True)
        dest = OWN / path.name
        dest.write_text(data)
        dest.chmod(mode)
        install_link(path, dest)
    refresh()


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
        check()
        if sys.argv[1] == "apply":
            apply()
        elif sys.argv[1] == "rollback":
            rollback()
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
