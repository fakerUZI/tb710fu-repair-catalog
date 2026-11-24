"""Stage the matching TB710FU pen module for the next boot."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory

from pogo_install import KERNEL


ROOT = Path(__file__).resolve().parent
MODROOT = Path("/lib/modules") / KERNEL
STATE = Path("/var/lib/tb710fu-pogo-pen")
DRIVER = "nvt_36xxx.ko"
# Published November 18 kernel and the separate double-tap repair use different
# modules. Keep each profile's display and gesture behaviour.
PROFILES = {
    "kernel/drivers/input/touchscreen/NT36XXX_SPI/" + DRIVER: (
        "stock", "504ff274844ffa17dca91c2c87fa405b637932b05ba8f628baee5d2ab3e0b0ea",
    ),
    "updates/tb710fu-screen-gestures/" + DRIVER: (
        "gestures", "36c1359b7013a6a2cdde36b1433291074d5c66b7c3eb59c271012e85d75551fa",
    ),
}


@dataclass(frozen=True)
class Plan:
    module: Path
    payload: Path
    original: str
    replacement: str
    installed: str


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def modinfo(field, module):
    return subprocess.check_output(
        ["/usr/sbin/modinfo", "-k", KERNEL, "-F", field, str(module)],
        text=True, timeout=15,
    ).strip()


def depmod():
    subprocess.run(["/usr/sbin/depmod", "-a", KERNEL], check=True, timeout=60)


def selected_module():
    path = Path(modinfo("filename", "nvt_36xxx"))
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("当前 nvt_36xxx 模块不是普通文件")
    return path.resolve(strict=True)


def saved_state():
    if not os.path.lexists(STATE):
        return None
    marker = STATE / "state.json"
    backup = STATE / "original.ko"
    if STATE.is_symlink() or any(path.is_symlink() or not path.is_file() for path in (marker, backup)):
        raise RuntimeError("手写笔备份目录不完整，请保留现场")
    state = json.loads(marker.read_text())
    if (not isinstance(state, dict) or state.get("module") not in PROFILES
            or type(state.get("keyboard_preexisting")) is not bool
            or state.get("original") != PROFILES[state["module"]][1]
            or sha(backup) != state["original"]):
        raise RuntimeError("手写笔备份校验失败")
    return state


def require_kernel():
    if platform.release() != KERNEL:
        raise RuntimeError(f"手写笔测试模块适用于 {KERNEL}，当前为 {platform.release()}")


def preflight(state=None):
    require_kernel()
    module = selected_module()
    root = MODROOT.resolve()
    if root not in module.parents:
        raise RuntimeError("当前 nvt_36xxx 不在匹配内核的模块目录")
    relative = module.relative_to(root).as_posix()
    if relative not in PROFILES:
        raise RuntimeError(f"尚未覆盖当前触控模块路径：{relative}")
    profile, original = PROFILES[relative]
    payload = ROOT / "driver" / "pen" / profile / DRIVER
    replacement = sha(payload)
    installed = sha(module)
    if modinfo("vermagic", payload).split()[:1] != [KERNEL] or modinfo("name", payload) != "nvt_36xxx":
        raise RuntimeError("手写笔模块的内核版本或模块名称不匹配")
    if state and (state["module"] != relative or state["replacement"] != replacement):
        raise RuntimeError("手写笔备份与当前测试包或触控配置不同")
    if installed != original and (not state or installed != replacement):
        raise RuntimeError("触控模块不匹配已核对的原版或双击唤醒版")
    return Plan(module, payload, original, replacement, installed)


def atomic_copy(source, destination):
    mode = stat.S_IMODE(destination.stat().st_mode)
    with NamedTemporaryFile(dir=destination.parent, prefix=".tb710fu-pen-", delete=False) as stream:
        temp = Path(stream.name)
        try:
            with source.open("rb") as content:
                shutil.copyfileobj(content, stream)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            temp.replace(destination)
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temp.unlink(missing_ok=True)


def publish_backup(plan, keyboard_preexisting):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".tb710fu-pogo-pen-", dir=STATE.parent) as tmp:
        stage = Path(tmp) / "state"
        stage.mkdir(mode=0o700)
        backup = stage / "original.ko"
        shutil.copyfile(plan.module, backup)
        backup.chmod(0o600)
        if sha(backup) != plan.original:
            raise RuntimeError("备份期间触控模块发生变化")
        state = {
            "module": plan.module.relative_to(MODROOT.resolve()).as_posix(),
            "original": plan.original,
            "replacement": plan.replacement,
            "keyboard_preexisting": keyboard_preexisting,
        }
        marker = stage / "state.json"
        marker.write_text(json.dumps(state, indent=2) + "\n")
        stage.rename(STATE)


def replace(plan, target, expected_before, expected_after):
    if sha(plan.module) != expected_before:
        raise RuntimeError("触控模块已被其他操作修改")
    try:
        atomic_copy(target, plan.module)
        depmod()
        if selected_module() != plan.module or sha(plan.module) != expected_after:
            raise RuntimeError("重建模块索引后选中的触控驱动与预期不同")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        # Keep the original backup available if even the recovery fails.
        recovery = STATE / "original.ko" if expected_before == plan.original else plan.payload
        if sha(plan.module) == expected_after and sha(recovery) == expected_before:
            atomic_copy(recovery, plan.module)
            depmod()
        raise


def stage(keyboard_preexisting):
    state = saved_state()
    plan = preflight(state)
    if not state:
        publish_backup(plan, keyboard_preexisting)
    if plan.installed != plan.replacement:
        replace(plan, plan.payload, plan.original, plan.replacement)
        return True
    return False


def rollback():
    require_kernel()
    state = saved_state()
    if not state:
        return
    plan = preflight(state)
    if plan.installed == plan.replacement:
        replace(plan, STATE / "original.ko", plan.replacement, plan.original)
    shutil.rmtree(STATE)
