#!/usr/bin/python3
"""Stage user-opt-in face unlock using the already working front camera."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
PREFIX = Path("/usr/local/lib/tb710fu-face-unlock")
STATE = Path("/var/lib/tb710fu-face-unlock")
BACKUP = Path("/var/backups/tb710fu-face-unlock-catalog")
SCHEMAS = Path("/usr/share/glib-2.0/schemas")
PAM = Path("/etc/pam.d/gdm-password")
DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNIZER = "face_recognition_sface_2021dec.onnx"
MODELS = {
    DETECTOR: "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    RECOGNIZER: "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}
PARTS = [f"sface-part-a{suffix}" for suffix in "abcde"]
FRONT = Path("/sys/firmware/devicetree/base/soc@0/cci@ac15000/i2c-bus@0/camera@39")
PACKAGES = ("python3-opencv", "python3-gi", "gir1.2-gtk-4.0", "gir1.2-gstreamer-1.0",
            "gstreamer1.0-libcamera", "gstreamer1.0-plugins-base", "pkexec", "libpam-modules")
FILES = {
    name: (PREFIX / name, mode)
    for name, mode in (("face-unlock.py", 0o644), ("pam-face-unlock", 0o755),
                       ("settings-admin", 0o755), ("manage", 0o755),
                       (DETECTOR, 0o644), (RECOGNIZER, 0o644))
}
SCHEMA = "org.tb710fu.face-unlock.gschema.xml"
POLICY = "org.tb710fu.face-unlock.policy"
DESKTOP = "tb710fu-face-unlock.desktop"
FILES.update({
    SCHEMA: (SCHEMAS / SCHEMA, 0o644),
    POLICY: (Path("/usr/share/polkit-1/actions") / POLICY, 0o644),
    DESKTOP: (Path("/usr/local/share/applications") / DESKTOP, 0o644),
    "face-settings.py": (Path("/usr/local/libexec/tb710fu-face-settings"), 0o755),
})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"安装路径异常：{path}")
    return path.read_bytes() if path.exists() else None


def put(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tb710fu-face-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def model(name):
    installed = read_file(PREFIX / name)
    if installed is not None and digest(installed) == MODELS[name]:
        return installed
    data = ((HERE / name).read_bytes() if name == DETECTOR
            else b"".join((HERE / part).read_bytes() for part in PARTS))
    if digest(data) != MODELS[name]:
        raise RuntimeError(f"识别模型附件校验失败：{name}")
    return data


def preflight(apply):
    pam = read_file(PAM)
    auth = (line.split() for line in (pam or b"").splitlines())
    if sum(line == [b"@include", b"common-auth"] for line in auth) != 1:
        raise RuntimeError("找不到唯一的 GDM 密码入口")
    if not os.access("/usr/libexec/gdm-session-worker", os.X_OK):
        raise RuntimeError("缺少 GDM 桌面登录组件")
    if not FRONT.is_dir():
        raise RuntimeError("当前系统没有可用的前摄设备树，请先恢复前摄")
    result = subprocess.run(("/usr/bin/dpkg-query", "-W", "-f=${Package} ${db:Status-Status}\\n", *PACKAGES),
                            text=True, capture_output=True, timeout=15)
    installed = {line.split()[0] for line in result.stdout.splitlines() if line.endswith(" installed")}
    missing = [name for name in PACKAGES if name not in installed]
    if missing:
        if not apply:
            raise RuntimeError("请运行安装以补齐依赖：" + "、".join(missing))
        subprocess.run(("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *missing),
                       check=True, timeout=300, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    for path in (PREFIX, STATE, BACKUP):
        if path.is_symlink() or (path.exists() and (not path.is_dir() or path.stat().st_uid != 0
                                                  or path.stat().st_mode & 0o022)):
            raise RuntimeError(f"目录权限异常：{path}")


def compile_schemas():
    subprocess.run(("/usr/bin/glib-compile-schemas", "--strict", str(SCHEMAS)), check=True, timeout=15)


def rollback(use_installed_manage=True):
    state = json.loads((BACKUP / "state.json").read_text())
    changes = []
    for name, record in state["changes"].items():
        target, _mode = FILES[name]
        actual = read_file(target)
        prior = read_file(BACKUP / name) if record["original"] else None
        if (prior is not None and digest(prior) != record["original"]
                or prior is None and record["original"]):
            raise RuntimeError(f"备份文件已变化：{name}")
        if actual is not None and digest(actual) not in (record["installed"], record["original"]):
            raise RuntimeError(f"安装文件已变化：{target}")
        changes.append((target, record, prior))
    if use_installed_manage and not state["had_pam_helpers"]:
        if (PREFIX / "manage").is_file():
            subprocess.run((str(PREFIX / "manage"), "disable"), check=True, timeout=10)
    for target, record, prior in changes:
        if prior is None:
            target.unlink(missing_ok=True)
        else:
            put(target, prior, record["mode"])
    if SCHEMA in state["changes"]:
        compile_schemas()
    clear_backup()


def clear_backup():
    for path in BACKUP.iterdir():
        path.unlink()
    BACKUP.rmdir()


def install(apply):
    preflight(apply)
    sources = {name: model(name) if name in MODELS else (HERE / name).read_bytes() for name in FILES}
    original = {name: read_file(target) for name, (target, _mode) in FILES.items()}
    with tempfile.TemporaryDirectory(prefix="tb710fu-face-check-") as tmp:
        staging = Path(tmp)
        for name in ("face-unlock.py", *MODELS):
            (staging / name).write_bytes(sources[name])
        subprocess.run(("/usr/bin/timeout", "--kill-after=1s", "15s", "/usr/bin/python3", "-I",
                        str(staging / "face-unlock.py"), "probe"), check=True, timeout=20)
    if not apply:
        print("人脸解锁的前摄、识别模型和 GDM 入口检查通过。")
        return
    changes = {name for name, (target, mode) in FILES.items()
               if original[name] != sources[name] or stat.S_IMODE(target.stat().st_mode) != mode}
    if changes:
        if BACKUP.exists():
            raise RuntimeError("已有修复备份，请先核对当前安装文件再回退或重试")
        STATE.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
            raise RuntimeError("人脸资料目录必须仅允许管理员访问")
        BACKUP.mkdir(mode=0o700)
        state = {"changes": {}, "had_pam_helpers": all(original[name] is not None for name in
                                                     ("face-unlock.py", "pam-face-unlock", "manage"))}
        try:
            for name in sorted(changes):
                target, _mode = FILES[name]
                prior = original[name]
                record = {"installed": digest(sources[name]), "original": None}
                if prior is not None:
                    record.update(original=digest(prior), mode=stat.S_IMODE(target.stat().st_mode))
                    put(BACKUP / name, prior, 0o600)
                state["changes"][name] = record
            put(BACKUP / "state.json", json.dumps(state).encode(), 0o600)
        except OSError:
            clear_backup()
            raise
        try:
            for name in FILES:
                if name in changes:
                    target, mode = FILES[name]
                    put(target, sources[name], mode)
            if SCHEMA in changes:
                compile_schemas()
        except (OSError, subprocess.SubprocessError):
            rollback(use_installed_manage=False)
            raise
    print("已安装人脸解锁。在应用菜单打开「人脸解锁」预览并录入。已有录入和启用状态已保留。")


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ("apply", "check", "rollback"):
        raise RuntimeError("请以管理员身份运行 apply、check 或 rollback")
    action = sys.argv[1]
    if action == "check":
        install(apply=False)
        return
    with open("/run/lock/tb710fu-face-catalog.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "rollback":
            rollback()
            print("已回退安装文件，平板上的人脸资料保留。")
        else:
            install(apply=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"人脸解锁：{error}") from None
