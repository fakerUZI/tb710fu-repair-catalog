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
        # The second policy patch builds on the previously published version.
        # Its reverse distinguishes a completed upgrade before testing the base.
        diffs = [Path(__file__).with_name(name) for name in patches]
        if not reverse and patch_source(source, diffs[-1], True) is not None:
            continue
        if reverse and len(diffs) == 2 and patch_source(source, diffs[0], False) is not None:
            continue
        # The base Pogo policy is also the independently released stable repair.
        # Rollback removes only this beta, keeping the stable policy available.
        for diff in (diffs[-1:] if reverse else diffs):
            updated = patch_source(source, diff, reverse)
            if updated is not None:
                source.write_bytes(updated)
            elif patch_source(source, diff, not reverse) is None:
                raise ValueError(f"现有扩展与补丁不匹配：{uuid} / {diff.name}")
        updated = source.read_bytes()
        if original != updated:
            changes[path] = original, updated
    return changes


def replace(path, contents):
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temp = tempfile.mkstemp(prefix=".pogo-osk-", dir=path.parent)
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
    if action == "check":
        with tempfile.TemporaryDirectory() as temp:
            changes = plan(Path(temp), False)
        print("可以更新屏幕键盘策略。" if changes else "当前扩展已处于目标状态。")
        return
    lock = os.open("/run/lock/tb710fu-wifi-password.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "r+") as held, tempfile.TemporaryDirectory() as temp:
        fcntl.flock(held, fcntl.LOCK_EX)
        changes = plan(Path(temp), action == "rollback")
        commit(changes)
    print("屏幕键盘已" + ("更新" if action == "apply" else "回退") +
          "。保存工作后注销并重新登录，即可载入当前版本。")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"屏幕键盘修复未完成：{error}")
