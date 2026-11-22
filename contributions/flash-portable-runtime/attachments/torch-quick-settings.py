#!/usr/bin/python3
"""Restore the GNOME quick toggle for the installed TB710FU torch service."""
import ast
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile


UUID = "tb-torch@tb710fu"
EXTENSIONS = Path("/usr/share/gnome-shell/extensions")
ATTACHMENTS = Path(__file__).resolve().parent
SHELL_SETTINGS = "org.gnome.shell"
FILES = ("metadata.json", "extension.js")


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(path):
    require(not path.is_symlink() and (not path.exists() or path.is_file()), f"异常扩展文件：{path}")
    return path.read_bytes() if path.exists() else None


def compatible(metadata, shell_version):
    if not isinstance(metadata, dict) or metadata.get("uuid") != UUID:
        return False
    versions = metadata.get("shell-version")
    return isinstance(versions, list) and any(
        isinstance(version, str) and version.split(".")[0] == shell_version for version in versions
    )


def make_readable(target):
    paths = {target: 0o055, **{target / name: 0o044 for name in FILES}}
    modes = {}
    for path, permissions in paths.items():
        info = path.stat()
        mode = stat.S_IMODE(info.st_mode)
        require(info.st_uid == os.geteuid() and not mode & 0o022, f"异常扩展权限：{path}")
        if mode | permissions != mode:
            modes[path] = mode | permissions
    for path, mode in modes.items():
        path.chmod(mode)
    return bool(modes)


def install_extension(shell_version):
    target = EXTENSIONS / UUID
    require(not target.is_symlink() and (not target.exists() or target.is_dir()), f"异常扩展目录：{target}")
    current = {name: read(target / name) for name in FILES}
    if all(current.values()):
        metadata = json.loads(current["metadata.json"])
        require(compatible(metadata, shell_version),
                f"已安装的手电筒扩展未声明支持 GNOME Shell {shell_version}")
        return make_readable(target)
    payload = {name: (ATTACHMENTS / f"torch-{name}").read_bytes() for name in FILES}
    metadata = json.loads(payload["metadata.json"])
    require(compatible(metadata, shell_version),
            f"随包扩展未声明支持 GNOME Shell {shell_version}")
    require(all(data is None or data == payload[name] for name, data in current.items()),
            "扩展文件不完整且与随包版本不同，需要核对已安装的版本")
    previous_umask = os.umask(0o022)
    try:
        EXTENSIONS.mkdir(parents=True, exist_ok=True)
    finally:
        os.umask(previous_umask)
    with tempfile.TemporaryDirectory(prefix=".tb710fu-torch-", dir=EXTENSIONS) as staging:
        stage = Path(staging)
        for name in FILES:
            source = stage / name
            source.write_bytes(payload[name])
            source.chmod(0o644)
        if not target.exists():
            stage.chmod(0o755)
            stage.rename(target)
        else:
            for name in FILES:
                if current[name] is None:
                    (stage / name).rename(target / name)
    make_readable(target)
    return True


def desktop_command():
    seat = run("/usr/bin/loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value")
    session = seat.stdout.strip()
    require(seat.returncode == 0 and session, "请登录本机 GNOME 桌面后运行修复")
    props = run("/usr/bin/loginctl", "show-session", session,
                "-p", "User", "-p", "Type", "-p", "Class", "-p", "Active", "-p", "Remote", "-p", "Seat")
    values = dict(line.split("=", 1) for line in props.stdout.splitlines() if "=" in line)
    require(props.returncode == 0 and values.get("Seat") == "seat0"
            and values.get("Type") in ("wayland", "x11") and values.get("Class") == "user"
            and values.get("Active") == "yes" and values.get("Remote") == "no",
            "请登录本机 GNOME 桌面后运行修复")
    uid = int(values["User"])
    caller = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or str(uid)
    require(uid > 0 and str(uid) == caller, "请用当前登录桌面的账号运行修复")
    user = pwd.getpwuid(uid)
    runtime = f"/run/user/{uid}"
    return ("/usr/sbin/runuser", "-u", user.pw_name, "--", "/usr/bin/env", f"HOME={user.pw_dir}",
            f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus", "LC_ALL=C")


def setting(command, action, key, *args):
    result = run(*command, "/usr/bin/gsettings", action, SHELL_SETTINGS, key, *args)
    require(result.returncode == 0, f"GNOME 设置操作失败：{key} {result.stderr.strip()}")
    return result.stdout.strip()


def add_preference(command):
    updates = {}
    for key in ("enabled-extensions", "disabled-extensions"):
        value = setting(command, "get", key)
        current = ast.literal_eval(value.removeprefix("@as "))
        require(isinstance(current, list) and all(isinstance(item, str) for item in current),
                f"无法解析 GNOME 扩展列表：{key}")
        if key == "enabled-extensions":
            changed = current if UUID in current else [*current, UUID]
        else:
            changed = [item for item in current if item != UUID]
        if changed != current:
            require(setting(command, "writable", key) == "true", f"GNOME 扩展设置已被锁定：{key}")
            updates[key] = json.dumps(changed, ensure_ascii=False)
    for key, value in updates.items():
        setting(command, "set", key, value)


def enable(command, installed):
    tool = (*command, "/usr/bin/gnome-extensions")
    if run(*tool, "info", UUID).returncode != 0:
        add_preference(command)
        print("已保存手电筒快捷开关设置。请注销并重新登录，让 GNOME 加载扩展。")
        return
    result = run(*tool, "enable", UUID)
    require(result.returncode == 0, f"无法启用手电筒扩展：{result.stderr.strip()}")
    info = run(*tool, "info", UUID)
    states = {line.partition(":")[2].strip() for line in info.stdout.splitlines()
              if line.strip().startswith("State:")}
    if "ACTIVE" in states:
        print("手电筒开关已启用，可在右上角快捷设置中使用。")
    elif states.intersection(("ERROR", "OUT_OF_DATE")) and not installed:
        raise RuntimeError(f"GNOME 无法加载现有手电筒扩展：{', '.join(sorted(states))}")
    else:
        print("手电筒快捷开关已启用。请解锁查看，仍未显示时请注销并重新登录。")


def main():
    require(os.geteuid() == 0, "需要管理员权限")
    command = desktop_command()
    shell = run("/usr/bin/gnome-shell", "--version")
    fields = shell.stdout.split()
    require(shell.returncode == 0 and len(fields) >= 3 and fields[:2] == ["GNOME", "Shell"],
            "未找到 GNOME Shell")
    installed = install_extension(fields[2].split(".")[0])
    require(setting(command, "get", "disable-user-extensions") == "false",
            "GNOME 已暂停所有用户扩展。请在「扩展」应用启用扩展后重试")
    enable(command, installed)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, SyntaxError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f"手电筒快捷设置：{error}")
