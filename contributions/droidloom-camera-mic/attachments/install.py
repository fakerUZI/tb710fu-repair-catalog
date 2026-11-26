"""Install the Droidloom camera and microphone bridges from the repair assistant."""
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SPEC = Path("/etc/droidloom/cell.json")
DEST = Path("/usr/lib/droidloom/tb710fu-camera-mic")
TARGETS = ("/vendor/bin/hw/android.hardware.camera.provider-service.droidloom",
           "/vendor/lib64/libdroidloom_minigbm.dylib.so")
LOCK = "/run/lock/tb710fu-droidloom-r49.lock"


def atomic(path, data, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()


def guest(action):
    original = SPEC.read_bytes()
    spec = json.loads(original)
    entries = spec.setdefault("android_file_overrides", [])
    before = DEST / "before.json"
    if action == "rollback" and not before.exists():
        return
    previous = json.loads(before.read_bytes()) if before.exists() else []
    if action == "apply":
        DEST.mkdir(mode=0o755, parents=True, exist_ok=True)
    for target in TARGETS:
        file = Path(target).name
        installed = {"source": str(DEST / file), "target": target}
        matches = [entry for entry in entries if entry["target"] == target]
        if action == "rollback" and installed not in matches:
            continue  # Keep any new binding installed by a runtime update.
        if action == "apply":
            if installed not in matches:
                previous = [e for e in previous if e["target"] != target] + matches
            atomic(DEST / file, (HERE / file).read_bytes(), 0o755)
        for entry in matches:
            entries.remove(entry)
        if action == "apply":
            entries.append(installed)
        else:
            entries.extend(entry for entry in previous if entry["target"] == target)
    if action == "apply":
        atomic(before, encode(previous), 0o600)
    if json.loads(original) != spec:
        if SPEC.read_bytes() != original:
            raise ValueError("容器配置刚被其他程序修改，请重试。")
        atomic(SPEC, encode(spec), SPEC.stat().st_mode & 0o777)
    if action == "rollback":
        for target in TARGETS:
            path = DEST / Path(target).name
            if not any(e["source"] == str(path) for e in entries):
                path.unlink(missing_ok=True)
        before.unlink()


def run(action):
    uid = int(json.loads(SPEC.read_bytes())["host_uid"])
    account = pwd.getpwuid(uid)
    if not uid:
        raise ValueError("容器缺少桌面账号。")
    user = [] if os.geteuid() == uid else ["runuser", "-u", account.pw_name, "--"]
    user += ["env", f"HOME={account.pw_dir}", f"XDG_RUNTIME_DIR=/run/user/{uid}",
             f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"]
    # The assistant's batch staging directory may be readable only by root.
    with tempfile.TemporaryDirectory(prefix="tb710fu-camera-", dir="/tmp") as directory:
        stage = Path(directory)
        for name in ("install.sh", "camera.py", "droidloom-audio"):
            shutil.copyfile(HERE / name, stage / name)
            (stage / name).chmod(0o644)
        stage.chmod(0o755)
        host = [*user, "/bin/sh", str(stage / "install.sh")]
        if action != "rollback":
            for target in TARGETS:
                with (HERE / Path(target).name).open("rb") as binary:
                    if binary.read(4) != b"\x7fELF":
                        raise ValueError("安卓相机附件不完整，请重新下载。")
        if action == "check":
            print("可安装。已找到 Droidloom 容器。")
            return
        running = subprocess.run([*user, "systemctl", "--user", "show", "droidloom.service",
                                  "-p", "ActiveState", "--value"], check=True,
                                 capture_output=True, text=True).stdout.strip() in ("active", "activating")
        ctl = [*user, "droidloomctl"]
        if running:
            subprocess.run([*ctl, "stop"], check=True)
        first_install = action == "apply" and not (DEST / "before.json").exists()
        try:
            subprocess.run([*host, action], check=True)
            guest(action)
            if running:
                subprocess.run([*ctl, "start"], check=True)
        except Exception:
            if first_install:
                if running:
                    subprocess.run([*ctl, "stop"])
                guest("rollback")
                subprocess.run([*host, "rollback"], check=True)
            if running:
                subprocess.run([*ctl, "start"])
            raise
    print("已应用安卓摄像头与麦克风修复。" if action == "apply" else "已回退至安装前的配置。")


def main(action):
    if action == "check":
        run(action)
    elif action in ("apply", "rollback"):
        if os.geteuid() != 0:
            raise ValueError("请使用修复助手的管理员授权。")
        with open(LOCK, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            run(action)
    else:
        raise ValueError("支持 check、apply 和 rollback。")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))
