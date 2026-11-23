"""Restore systemd suspend using the kernel's s2idle mode."""

import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile

TARGETS = ("sleep.target", "suspend.target")
UNIT_DIRS = (Path("/etc/systemd/system"), Path("/run/systemd/system"))
CONFIG = Path("/etc/systemd/sleep.conf.d/99-tb710fu-s2idle.conf")
SETTINGS = "[Sleep]\nAllowSuspend=yes\nSuspendState=mem\nMemorySleepMode=s2idle\n"
STATE_DIR = Path("/var/lib/tb710fu-suspend-restore")


def run(*args):
    return subprocess.check_output(args, text=True, timeout=20).strip()


def flags(path):
    return {value.strip("[]") for value in Path(path).read_text().split()}


def check_support():
    if "mem" not in flags("/sys/power/state") or "s2idle" not in flags("/sys/power/mem_sleep"):
        raise ValueError("当前内核未提供 mem/s2idle 挂起。")
    version = run("systemctl", "--version").split()[1]
    if int(version) < 256:
        raise ValueError("当前 systemd 尚不支持 MemorySleepMode 配置。")


def present(path):
    return path.exists() or path.is_symlink()


def is_mask(path):
    return path.is_symlink() and path.readlink() == Path("/dev/null")


def unit_paths():
    return [directory / unit for directory in UNIT_DIRS for unit in TARGETS]


def loaded_units():
    return {
        unit: run("systemctl", "show", unit, "--property=LoadState", "--value")
        for unit in TARGETS
    }


def effective_s2idle():
    settings = {}
    section = ""
    text = run("systemd-analyze", "--no-pager", "cat-config", "systemd/sleep.conf").replace("\\\n", " ")
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Sleep" and line and not line.startswith(("#", ";")):
            key, equals, value = line.partition("=")
            if equals:
                settings[key.strip()] = value.strip()
    states = shlex.split(settings.get("SuspendState", "mem standby freeze"))
    modes = shlex.split(settings.get("MemorySleepMode", ""))
    allowed = settings.get("AllowSuspend", "yes").lower() not in ("no", "false", "off", "0", "n")
    return allowed and states[:1] == ["mem"] and modes[:1] == ["s2idle"]


def suspend_available():
    return run("busctl", "--system", "call", "org.freedesktop.login1", "/org/freedesktop/login1",
               "org.freedesktop.login1.Manager", "CanSuspend") in ('s "yes"', 's "challenge"')


def inspect():
    masks = [path for path in unit_paths() if is_mask(path)]
    status = loaded_units()
    for unit, state in status.items():
        if state != "loaded" and (state != "masked" or all(path.name != unit for path in masks)):
            raise ValueError(f"{unit} 状态为 {state}，请检查本机的 systemd 单元。")
    return masks, effective_s2idle()


def atomic_create(path, contents, mode):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(contents)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
            os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def check_config_ownership():
    if present(CONFIG) and (CONFIG.is_symlink() or not CONFIG.is_file() or CONFIG.read_text() != SETTINGS):
        raise ValueError(f"已存在或另有改动的本地配置：{CONFIG}，请先核对。")


def recover(record, state_path):
    masks, owns_config = record
    if owns_config:
        check_config_ownership()
    for path in masks:
        if present(path) and not is_mask(path):
            raise ValueError(f"已存在其他 systemd 单元：{path}")
    for path in masks:
        if not present(path):
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            path.symlink_to("/dev/null")
    if owns_config:
        CONFIG.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    state_path.unlink()


def read_state(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("本机回退记录格式无效。")
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or data.get("version") != 1 or
            not isinstance(data.get("masks"), list) or type(data.get("owns_config")) is not bool):
        raise ValueError("本机回退记录格式无效。")
    known = {str(path): path for path in unit_paths()}
    masks = [known.get(path) for path in data["masks"]]
    if any(path is None for path in masks) or len(set(masks)) != len(masks):
        raise ValueError("本机回退记录中的单元路径无效。")
    return masks, data["owns_config"]


def apply(state_path):
    masks, ready = inspect()
    previous = read_state(state_path) if present(state_path) else None
    if ready and not masks and suspend_available():
        print("已启用 s2idle 挂起，无需修改。")
        return
    if previous is None:
        if not ready and present(CONFIG):
            raise ValueError(f"本地睡眠配置已有改动：{CONFIG}，请先核对。")
        if ready and not masks:
            raise ValueError("logind 当前未提供挂起，请检查其他系统限制。")
        record = masks, not ready
        atomic_create(state_path, json.dumps({"version": 1, "masks": [str(path) for path in masks],
                                              "owns_config": not ready}) + "\n", 0o600)
    else:
        record = previous
        if set(masks) - set(record[0]):
            raise ValueError("出现了额外的本地屏蔽，请先核对。")
        if record[1]:
            check_config_ownership()
    try:
        if record[1] and not present(CONFIG):
            CONFIG.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            atomic_create(CONFIG, SETTINGS, 0o644)
        if not effective_s2idle():
            raise ValueError("现有睡眠配置覆盖了 s2idle，请检查 systemd 睡眠配置。")
        for mask in masks:
            if not is_mask(mask):
                raise ValueError(f"单元在执行期间发生变更：{mask}")
            mask.unlink()
        run("systemctl", "daemon-reload")
        if set(loaded_units().values()) != {"loaded"} or not suspend_available():
            raise ValueError("logind 未确认挂起可用。")
    except (OSError, ValueError, subprocess.SubprocessError):
        recover(record, state_path)
        raise
    print("已启用 s2idle 挂起。可从 GNOME 电源菜单手动挂起。")


def main(action):
    if action not in ("check", "apply", "rollback"):
        raise ValueError("操作须为 check、apply 或 rollback。")
    if action != "rollback":
        check_support()
    if action == "check":
        masks, ready = inspect()
        conditions = "、".join(str(path) for path in masks) or "无"
        print(f"本地挂起屏蔽：{conditions}")
        print("s2idle 挂起配置：" + ("已设置" if ready else "待设置"))
        print("logind 挂起：" + ("可用" if suspend_available() else "未开放"))
        return
    if os.geteuid() != 0:
        raise PermissionError("应用和回退需要管理员权限。")
    if action == "apply":
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not present(STATE_DIR):
        raise ValueError("没有本条目写入的回退记录。")
    info = STATE_DIR.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError("本机回退目录必须由管理员独占。")
    lock_path = STATE_DIR / "lock"
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = STATE_DIR / "before.json"
        if action == "apply":
            apply(state_path)
        else:
            if not present(state_path):
                raise ValueError("没有本条目写入的回退记录。")
            recover(read_state(state_path), state_path)
            print("已恢复应用前的挂起设置。")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) == 2 else "check")
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        sys.exit(f"挂起修复未完成：{error}")
