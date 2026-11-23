"""Restore the GNOME Wi-Fi secret service and touch password entry."""

import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


EXTENSIONS = Path("/usr/share/gnome-shell/extensions")
SOURCES = {
    "tb-osk-input-policy@tb710fu": "input-policy.patch",
    "tb-wowtype-osk@tb710fu": "wowtype.patch",
}
PACKAGES = ("gnome-keyring", "libpam-gnome-keyring", "patch")


def missing_packages():
    missing = []
    for package in PACKAGES:
        result = subprocess.run(
            ["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}", package],
            text=True, capture_output=True, check=False,
        )
        if result.returncode or result.stdout.strip() != "installed":
            missing.append(package)
    return missing


def patch_source(source, diff, reverse):
    options = ["--force", "--fuzz=0"]
    if reverse:
        options.append("--reverse")
    result = subprocess.run(
        ["/usr/bin/patch", *options, "--output=-", source, diff],
        capture_output=True, check=False,
    )
    return result.stdout if result.returncode == 0 else None


def plan(workspace, reverse):
    changes = {}
    for uuid, filename in SOURCES.items():
        path = EXTENSIONS / uuid / "extension.js"
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"找不到 TB710FU 屏幕键盘组件：{uuid}")
        if path.resolve() != path or not path.is_file() or path.stat().st_uid != 0:
            raise ValueError(f"屏幕键盘组件需要人工核对：{uuid}")
        original = path.read_bytes()
        source = workspace / filename
        source.write_bytes(original)
        diff = Path(__file__).with_name(filename)
        updated = patch_source(source, diff, reverse)
        if updated is not None:
            changes[path] = original, updated
        elif patch_source(source, diff, not reverse) is None:
            raise ValueError(f"现有扩展内容不同，请核对后保留现有自定义：{uuid}")
    return changes


def replace(path, contents):
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temp = tempfile.mkstemp(prefix=".wifi-password-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(contents)
            os.fchmod(writer.fileno(), mode)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def commit(changes):
    installed = []
    try:
        for path, (original, updated) in changes.items():
            if path.resolve() != path or path.read_bytes() != original:
                raise ValueError(f"扩展在修复期间发生变更：{path.parent.name}")
            replace(path, updated)
            installed.append(path)
    except (OSError, ValueError):
        for path in reversed(installed):
            original, updated = changes[path]
            if path.resolve() == path and path.read_bytes() == updated:
                replace(path, original)
        raise


def main(action):
    if action not in ("check", "apply", "rollback"):
        raise ValueError("操作须为 check、apply 或 rollback。")
    if action != "check" and os.geteuid() != 0:
        raise PermissionError("应用和回退需要管理员权限。")
    missing = missing_packages()
    if action == "check":
        print(f"缺少的软件包：{', '.join(missing) if missing else '无'}")
        if "patch" in missing:
            return
        with tempfile.TemporaryDirectory() as temp:
            pending = plan(Path(temp), False)
        print(f"待更新的屏幕键盘组件：{len(pending)}")
        return
    lock = os.open("/run/lock/tb710fu-wifi-password.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "r+") as held, tempfile.TemporaryDirectory() as temp:
        fcntl.flock(held, fcntl.LOCK_EX)
        if action == "apply" and missing:
            subprocess.run(
                ["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *missing],
                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"}, check=True,
            )
        changes = plan(Path(temp), action == "rollback")
        commit(changes)
    if action == "apply":
        print("GNOME 密钥环依赖已就绪。")
    print("屏幕键盘已" + ("更新" if action == "apply" else "回退") +
          "。保存工作后注销并重新登录，即可载入当前版本。")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"Wi-Fi 密码修复未完成：{error}")
