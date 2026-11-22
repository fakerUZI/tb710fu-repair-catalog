#!/usr/bin/python3
"""Restore the service startup inputs missing from a sanitized Ubuntu image."""

import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

SPOOL = Path("/var/spool/anacron")
SNAP_DIR = Path("/var/lib/snapd")
SNAP_STATE = SNAP_DIR / "state.json"
SNAP_CACHE = SNAP_DIR / "cache"
GENERIC_MODEL = {"brand": "generic", "model": "generic-classic"}
MODEL_ASSERTION = SNAP_DIR / "assertions/asserts-v0/model/16/generic/generic-classic/active"
SNAP_UNITS = ("snapd.seeded.service", "snapd.socket", "snapd.service")
MACRO = Path("/usr/local/libexec/tb-macro-hardware.py")
RESTORE_AUDIO = "  for uid,units in active:userctl(uid,'start','--no-block',*units)\n"
GUARDED_RESTORE = """  for uid,units in active:
   try:
    userctl(uid,'start','--no-block',*units)
   except subprocess.CalledProcessError:
    # Login and system shutdown can remove the user manager during camera teardown.
    manager=run('systemctl','show','--property=ActiveState','--value',f'user@{uid}.service',check=False)
    if manager.returncode==0 and manager.stdout.strip() in ('inactive','deactivating','failed'):
     continue
    if run('systemctl','is-system-running',check=False).stdout.strip()!='stopping':
     raise
"""


def snap_changes(document):
    data = document["data"]
    if data.get("seeded") is not True:
        return []
    changes = []
    auth = data.get("auth") or {}
    device = auth.get("device") or {}
    if not device.get("brand") and not device.get("model") and not device and MODEL_ASSERTION.is_file():
        data["auth"] = {**auth, "device": GENERIC_MODEL}
        changes.append("恢复 Snap 通用型号")
    for name in ("changes", "tasks"):
        if document.get(name) is None:
            document[name] = {}
            changes.append(f"补齐 Snap {name} 表")
    return changes


def read_snap_state():
    if SNAP_STATE.is_symlink():
        raise ValueError("Snap 状态文件是符号链接。")
    return json.loads(SNAP_STATE.read_text())


def missing_directory(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"路径不是普通目录：{path}")
    return not path.exists()


def replace(path, text):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"路径不是普通文件：{path}")
    previous = path.stat()
    backups = Path("/var/backups")
    if missing_directory(backups):
        backups.mkdir(mode=0o755, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="tb710fu-startup-", dir=backups))
    shutil.copy2(path, backup / path.name)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(text)
            output.flush()
            os.fchmod(output.fileno(), stat.S_IMODE(previous.st_mode))
            os.fchown(output.fileno(), previous.st_uid, previous.st_gid)
            os.fsync(output.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    print(f"原文件备份：{backup / path.name}", flush=True)


def systemctl(*args):
    subprocess.run(["/usr/bin/systemctl", *args], check=True, timeout=85)


def recover_snap():
    systemctl("stop", *SNAP_UNITS)
    try:
        descriptor = os.open(SNAP_DIR / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = read_snap_state()
            if snap_changes(state):
                replace(SNAP_STATE, json.dumps(state) + "\n")
    finally:
        systemctl("reset-failed", *SNAP_UNITS)
        systemctl("start", *SNAP_UNITS)
    subprocess.run(["/usr/bin/snap", "list"], check=True, timeout=20)


def repair(action):
    make_spool = Path("/usr/sbin/anacron").is_file() and missing_directory(SPOOL)
    has_snap = SNAP_STATE.is_file()
    make_cache = has_snap and missing_directory(SNAP_CACHE)
    state_changes = snap_changes(read_snap_state()) if has_snap else []
    macro = MACRO.read_text() if MACRO.is_file() and not MACRO.is_symlink() else ""
    patched_macro = None
    if macro.count(RESTORE_AUDIO) == 1:
        patched_macro = macro.replace(RESTORE_AUDIO, GUARDED_RESTORE)
        compile(patched_macro, str(MACRO), "exec")
    elif macro and GUARDED_RESTORE not in macro:
        print("现有微距脚本使用其他关机流程，保留该脚本。", flush=True)

    if make_spool:
        print("恢复 anacron 工作目录", flush=True)
    if make_cache:
        print("恢复 Snap 缓存目录", flush=True)
    for change in state_changes:
        print(change, flush=True)
    if patched_macro is not None:
        print("修复微距服务关机或注销时恢复音频会话的报错", flush=True)
    if not (make_spool or make_cache or state_changes or patched_macro is not None):
        print("所检查的服务无需修复。")
        return
    if action == "check":
        return
    if make_spool:
        SPOOL.mkdir(mode=0o755, exist_ok=True)
    if make_cache:
        SNAP_CACHE.mkdir(mode=0o700, exist_ok=True)
    if patched_macro is not None:
        replace(MACRO, patched_macro)
    if state_changes:
        recover_snap()
    print("修复完成。")


if __name__ == "__main__":
    try:
        if os.geteuid() != 0:
            raise ValueError("需要管理员权限。")
        if len(sys.argv) != 2 or sys.argv[1] not in ("apply", "check"):
            raise ValueError("此脚本支持检查和应用，备份用于离线恢复。")
        with open("/run/lock/tb710fu-bootfix-startup.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            repair(sys.argv[1])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
