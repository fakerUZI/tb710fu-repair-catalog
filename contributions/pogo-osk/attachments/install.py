"""Apply the Pogo input policy and the matching panel action together."""

import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


EXTENSIONS = Path("/usr/share/gnome-shell/extensions")
POLICY = "tb-osk-input-policy@tb710fu"
TOGGLE = "tb-osk-toggle@tb710fu"
ASSETS = Path(__file__).parent
UNIT = "tb710fu-pogo-fold@"
SYSTEM_FILES = {
    Path("/usr/local/libexec/tb710fu-pogo-fold-reader.py"): "fold-reader.py",
    Path(f"/etc/systemd/system/{UNIT}.service"): f"{UNIT}.service",
    Path("/etc/udev/rules.d/90-tb710fu-pogo-fold.rules"): "90-tb710fu-pogo-fold.rules",
}


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
    for uuid, patches in ((POLICY, ("input-policy.patch", "fold-policy.patch")),
                          (TOGGLE, ("panel-toggle.patch",))):
        path = EXTENSIONS / uuid / "extension.js"
        if path.resolve() != path or not path.is_file() or path.stat().st_uid != 0:
            raise ValueError(f"屏幕键盘组件需要人工核对：{uuid}")
        original = path.read_bytes()
        source = workspace / uuid
        source.write_bytes(original)
        diffs = [ASSETS / name for name in patches]
        if uuid == POLICY:
            diffs.append(ASSETS / "evdev-policy.patch")
        # Identify the deepest installed layer before applying or removing later layers.
        depth = 0
        for index in reversed(range(len(diffs))):
            if patch_source(source, diffs[index], True) is not None:
                depth = index + 1
                break
        if depth == 0 and patch_source(source, diffs[0], False) is None:
            raise ValueError(f"现有扩展与补丁不匹配：{uuid}")
        pending = reversed(diffs[:depth]) if reverse else diffs[depth:]
        for diff in pending:
            updated = patch_source(source, diff, reverse)
            if updated is None:
                raise ValueError(f"现有扩展与补丁不匹配：{uuid} / {diff.name}")
            source.write_bytes(updated)
        updated = source.read_bytes()
        if original != updated:
            changes[path] = original, updated
    return changes


def previous(path):
    if path.resolve() != path:
        raise ValueError(f"安装路径含有符号链接：{path}")
    if not path.exists():
        return None
    info = path.stat()
    if info.st_uid != 0 or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"安装路径需要人工核对：{path}")
    return path.read_bytes()


def system_plan(reverse):
    changes = {}
    for path, asset in SYSTEM_FILES.items():
        old = previous(path)
        packaged = (ASSETS / asset).read_bytes()
        if old not in (None, packaged):
            raise ValueError(f"系统中存在不同版本的文件：{path}")
        target = None if reverse else packaged
        if old != target:
            changes[path] = old, target
    return changes


def sync_readers():
    def run(*argv):
        subprocess.run(argv, check=True, timeout=20)

    run("systemctl", "daemon-reload")
    run("udevadm", "control", "--reload")
    if not all(p.is_file() for p in SYSTEM_FILES):
        run("systemctl", "stop", UNIT + "*.service")
        return
    for event in Path("/sys/class/input").glob("event*"):
        try:
            dev = event / "device"
            identity = [(dev / field).read_text().strip()
                        for field in ("phys", "id/bustype", "id/vendor", "id/product")]
        except FileNotFoundError:
            continue
        if identity == ["input/lenovo_kb", "0019", "17ef", "61ba"]:
            run("udevadm", "trigger", "--action=change", "--settle", str(event))
            if event.exists():
                run("systemctl", "start", UNIT + event.name + ".service")


def replace(path, contents):
    if contents is None:
        path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temp = tempfile.mkstemp(prefix=".pogo-osk-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(contents)
            os.fchmod(writer.fileno(), mode)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def commit(changes, sync=False):
    installed = []
    try:
        for path, (original, updated) in changes.items():
            if previous(path) != original:
                raise ValueError(f"扩展在修复期间发生变更：{path.parent.name}")
            replace(path, updated)
            installed.append(path)
        if sync:
            sync_readers()
    except (OSError, ValueError, subprocess.SubprocessError):
        for path in reversed(installed):
            original, updated = changes[path]
            if previous(path) == updated:
                replace(path, original)
        if sync and installed:
            sync_readers()
        raise


def main(action):
    if action not in ("check", "apply", "rollback"):
        raise ValueError("操作须为 check、apply 或 rollback。")
    if action != "check" and os.geteuid() != 0:
        raise PermissionError("应用和回退需要管理员权限。")
    if action == "check":
        with tempfile.TemporaryDirectory() as temp:
            changes = plan(Path(temp), False) | system_plan(False)
        print("可以更新屏幕键盘策略。" if changes else "当前扩展已处于目标状态。")
        return
    lock = os.open("/run/lock/tb710fu-wifi-password.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "r+") as held, tempfile.TemporaryDirectory() as temp:
        fcntl.flock(held, fcntl.LOCK_EX)
        changes = plan(Path(temp), action == "rollback")
        commit(changes | system_plan(action == "rollback"), sync=True)
    print("屏幕键盘已" + ("更新" if action == "apply" else "回退") +
          "。保存工作后注销并重新登录，即可载入当前版本。")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"屏幕键盘修复未完成：{error}")
