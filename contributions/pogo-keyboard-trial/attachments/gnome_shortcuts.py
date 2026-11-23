"""Temporarily bind the Pogo window keys in the active GNOME session."""

from ast import literal_eval
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile


STATE = Path("/var/lib/tb710fu-pogo-trial")
BINDINGS = (
    ("org.gnome.desktop.wm.keybindings", "toggle-fullscreen", "XF86FullScreen"),
    ("org.gnome.mutter.keybindings", "toggle-tiled-left", "XF86AspectRatio"),
)


def read_array(value):
    if not value:
        return []
    items = literal_eval(value.removeprefix("@as "))
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError("GNOME 快捷键设置不是字符串列表")
    return items


class GnomeShortcuts:
    def __init__(self, account):
        uid = account.pw_uid
        runtime = f"/run/user/{uid}"
        self.options = {
            "user": uid, "group": account.pw_gid,
            "extra_groups": os.getgrouplist(account.pw_name, account.pw_gid),
            "env": {
                "PATH": "/usr/bin:/bin", "HOME": account.pw_dir,
                "XDG_RUNTIME_DIR": runtime,
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
            },
        }
        self.state = STATE / f"shortcuts-{uid}.json"

    @classmethod
    def active(cls):
        session = subprocess.check_output(
            ["/usr/bin/loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value"],
            text=True, timeout=4,
        ).strip()
        if not session:
            return None
        info = dict(line.split("=", 1) for line in subprocess.check_output(
            ["/usr/bin/loginctl", "show-session", session, "-p", "User", "-p", "Class", "-p", "Type"],
            text=True, timeout=4,
        ).splitlines())
        if info.get("Class") != "user" or info.get("Type") != "wayland" or int(info["User"]) == 0:
            return None
        shortcuts = cls(pwd.getpwuid(int(info["User"])))
        shell = shortcuts.run(
            "gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
            "--object-path", "/org/freedesktop/DBus", "--method",
            "org.freedesktop.DBus.NameHasOwner", "org.gnome.Shell",
        )
        return shortcuts if shell == "(true,)" else None

    def run(self, command, *args):
        return subprocess.check_output(
            [f"/usr/bin/{command}", *args], text=True, stderr=subprocess.PIPE,
            timeout=4, **self.options,
        ).strip()

    def apply(self):
        changes = {}
        for schema, key, accelerator in BINDINGS:
            path = f"/{schema.replace('.', '/')}/{key}"
            effective = read_array(self.run("gsettings", "get", schema, key))
            if accelerator in effective:
                continue
            if self.run("gsettings", "writable", schema, key) != "true":
                raise RuntimeError(f"GNOME 快捷键 {path} 不可写")
            changes[path] = {
                "original": self.run("dconf", "read", path),
                "added": accelerator, "trial": [*effective, accelerator],
            }
        if changes:
            self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.state.parent) as work:
                saved = Path(work) / self.state.name
                saved.write_text(json.dumps(changes))
                saved.replace(self.state)
            for path, change in changes.items():
                self.run("dconf", "write", path, repr(change["trial"]))
                if read_array(self.run("dconf", "read", path)) != change["trial"]:
                    raise RuntimeError(f"无法启用 GNOME 快捷键 {path}")

    def restore(self):
        if not self.state.is_file():
            return
        for path, change in json.loads(self.state.read_text()).items():
            current = read_array(self.run("dconf", "read", path))
            if change["added"] not in current:
                continue
            if current != change["trial"]:
                current.remove(change["added"])
                self.run("dconf", "write", path, repr(current))
            elif change["original"]:
                self.run("dconf", "write", path, change["original"])
            else:
                self.run("dconf", "reset", path)
            if change["added"] in read_array(self.run("dconf", "read", path)):
                raise RuntimeError(f"无法撤回 GNOME 快捷键 {path}")
        self.state.unlink()
