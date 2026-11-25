"""Repair Assistant entry point for the per-user GNOME Shell extension."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from gi.repository import Gio, GLib

SOURCE = Path(__file__).resolve().parent / "extension"
FILES = ("metadata.json", "extension.js", "desktop-keys.js", "touchpad.js",
         "primary-monitor.js", "stylesheet.css")
META = json.loads((SOURCE / "metadata.json").read_bytes())
UUID = META["uuid"]
TARGET = Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions" / UUID


def matches():
    return all((TARGET / f).is_file() and (TARGET / f).read_bytes() == (SOURCE / f).read_bytes()
               for f in FILES)


def enabled(settings):
    return (UUID in settings.get_strv("enabled-extensions") and
            UUID not in settings.get_strv("disabled-extensions"))


def set_enabled(settings, enable):
    settings.delay()
    for key, want in (("enabled-extensions", enable), ("disabled-extensions", not enable)):
        values = settings.get_strv(key)
        if (UUID in values) == want:
            continue
        updated = values + [UUID] if want else [item for item in values if item != UUID]
        if not settings.set_strv(key, updated):
            raise SystemExit("无法更新当前用户的 GNOME 扩展设置。")
    settings.apply()
    Gio.Settings.sync()


def main(action):
    if action not in ("check", "apply", "rollback"):
        raise SystemExit("可用动作：check、apply、rollback。")
    if os.geteuid() == 0:
        raise SystemExit("请在平板桌面的当前用户会话中运行。")
    settings = Gio.Settings.new("org.gnome.shell")
    if action == "check":
        print("扩展文件：", "与本条目一致" if matches() else "有差异" if TARGET.exists() else "尚未安装")
        print("用户启用状态：", "已启用" if enabled(settings) else "未启用")
        return
    if action == "rollback":
        if TARGET.exists() or enabled(settings):
            set_enabled(settings, False)
        print("已停用外接桌面控制。用户目录内的扩展文件保留，可重新运行安装以启用。")
        return

    shell = subprocess.check_output(["gnome-shell", "--version"], text=True).strip()
    if not re.search(r"\b50(?:\.|$)", shell):
        raise SystemExit(f"本版本适用于 GNOME Shell 50，当前为 {shell}。")
    if settings.get_boolean("disable-user-extensions"):
        raise SystemExit("系统已关闭用户扩展。请先在“扩展”中打开总开关。")
    if not matches():
        if (TARGET / "metadata.json").is_file():
            old = json.loads((TARGET / "metadata.json").read_bytes())
            if old.get("version", 0) > META["version"]:
                raise SystemExit("已安装更新版本，保留现有版本。")
        subprocess.run(["/bin/sh", str(SOURCE / "install.sh")], check=True)
    if not matches():
        raise SystemExit("安装后的扩展文件与下载内容不一致。")
    if not enabled(settings):
        set_enabled(settings, True)
    if not enabled(settings):
        raise SystemExit("扩展未能启用。")
    print(f"已核对并启用外接桌面控制 v{META['version']}。初次安装或更新后请保存工作并重新登录。")


if __name__ == "__main__":
    main(sys.argv[1])
