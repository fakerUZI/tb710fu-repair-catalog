"""Install the paired release-49 pen presenter and Android input receiver."""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

ASSETS = Path(__file__).resolve().parent
ROOT = Path("/usr/local/libexec/tb710fu-droidloom-native-pen")
LIVE = Path("/usr/lib/droidloom/runtime/lib64/libinputflinger.so")
SPEC = Path("/etc/droidloom/cell.json")
META = Path("/usr/share/droidloom/package.json")
STOCK = Path("/usr/bin/droidloom-wayland")
FACTORY_SHA = "a61a37dc57fc5ec2bb7b80366452329ae9576426bcd65cec94d576a36dc3cc3d"
PREVIOUS_PRESENTER_SHA = "fb23637ff0b46169926492d7991bf76ff1e41386cf6bba02b091f3c7db47bd71"
MARKER = b"DROIDLOOM_STYLUS_WIRE=1;"
HOST = "droidloom-wayland"
GUEST = "libinputflinger.so"
DROPIN = f"[Service]\nExecStart=\nExecStart=/usr/bin/python3 -IB {ROOT}/install.py launch\n"


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def release_matches():
    info = json.loads(META.read_bytes())
    return (info.get("architecture"), info.get("version"), info.get("release")) == ("aarch64", "0.1.0", 49)


def check_guest_binding():
    spec = json.loads(SPEC.read_bytes())
    overrides = [entry for entry in spec.get("android_file_overrides", [])
                 if isinstance(entry, dict) and entry.get("target") == "/system/lib64/libinputflinger.so"]
    if len(overrides) != 1 or Path(overrides[0].get("source", "")).resolve() != LIVE:
        raise ValueError("当前容器使用不同的安卓输入库。")


def unpack(folder, name, spec):
    parts = sorted(ASSETS.glob(name + ".gz.[0-9][0-9][0-9]"))
    if [p.name for p in parts] != [f"{name}.gz.{i:03d}" for i in range(1, spec["parts"] + 1)]:
        raise ValueError(f"{name} 缺少分片，请重新下载。")
    with tempfile.TemporaryFile() as compressed:
        for part in parts:
            with part.open("rb") as stream:
                shutil.copyfileobj(stream, compressed)
        compressed.seek(0)
        with gzip.GzipFile(fileobj=compressed) as source:
            data = source.read(spec["size"] + 1)
    if len(data) != spec["size"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError(f"{name} 重组校验失败。")
    if data[:6] != b"\x7fELF\x02\x01" or data[18:20] != b"\xb7\x00" or (name == GUEST and MARKER not in data):
        raise ValueError(f"{name} 缺少 ARM64 原生笔支持。")
    (folder / name).write_bytes(data)
    (folder / name).chmod(0o755)


def desktop_action(account, action):
    runtime = f"/run/user/{account.pw_uid}"
    subprocess.run(["runuser", "-u", account.pw_name, "--", "env", f"HOME={account.pw_dir}",
                    f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
                    "/usr/bin/python3", "-IB", str(ROOT / "install.py"), action], check=True)


def configure(action):
    if os.geteuid() == 0:
        raise ValueError("桌面覆盖需由桌面账号管理。")
    systemctl = ["systemctl", "--user"]
    environment = subprocess.check_output([*systemctl, "show-environment"], text=True).splitlines()
    config = next((line.split("=", 1)[1] for line in environment if line.startswith("XDG_CONFIG_HOME=")), "")
    folder = (Path(config) if config else Path.home() / ".config") / "systemd/user/droidloom.service.d"
    override = folder / "95-tb710fu-native-pen.conf"
    if override.is_symlink() or (override.exists() and override.read_text() != DROPIN):
        raise ValueError(f"已有不同的覆盖配置 {override}")
    if action == "disable":
        override.unlink(missing_ok=True)
    else:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, mode="w", delete=False) as staged:
            staged.write(DROPIN)
        os.replace(staged.name, override)
    subprocess.run([*systemctl, "daemon-reload"], check=True)
    if action == "enable":
        active = subprocess.check_output([*systemctl, "show", "droidloom.service", "-p", "ExecStart", "--value"], text=True)
        if f"argv[]=/usr/bin/python3 -IB {ROOT}/install.py launch ;" not in active:
            raise ValueError("另一个配置覆盖了 Droidloom 启动命令。")


def swap_receiver(payload):
    with tempfile.TemporaryDirectory(dir=LIVE.parent) as folder:
        candidate = Path(folder) / GUEST
        # Preserve the factory file's owner, label and extended attributes.
        original = LIVE.stat()
        shutil.copyfile(payload, candidate)
        os.chown(candidate, original.st_uid, original.st_gid)
        shutil.copystat(LIVE, candidate)
        os.replace(candidate, LIVE)


def stage(account, release):
    ROOT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT.parent) as folder:
        pending = Path(folder) / "install"
        pending.mkdir(mode=0o755)
        for name in (HOST, GUEST):
            unpack(pending, name, release[name])
        shutil.copyfile(LIVE, pending / "factory.so")
        if digest(pending / "factory.so") != FACTORY_SHA:
            raise ValueError("暂存时系统输入库发生变化。")
        shutil.copyfile(ASSETS / "install.py", pending / "install.py")
        (pending / "state.json").write_text(json.dumps({"uid": account.pw_uid, "guest": release[GUEST]["sha256"]}))
        for name in ("install.py", "state.json"):
            (pending / name).chmod(0o644)
        pending.chmod(0o755)
        pending.rename(ROOT)


def apply_or_rollback(account, action, release):
    current = digest(LIVE)
    if ROOT.is_symlink():
        raise ValueError("原生笔安装目录异常。")
    if not ROOT.exists():
        if action == "rollback":
            print("本机没有该修复的备份。")
            return
        if current != FACTORY_SHA:
            raise ValueError("当前安卓输入库与 release 49 原版不匹配。")
        stage(account, release)
    state = json.loads((ROOT / "state.json").read_text())
    if state["uid"] != account.pw_uid or digest(ROOT / "factory.so") != FACTORY_SHA:
        raise ValueError("请核对备份及最初安装本修复的桌面账号。")
    if action == "rollback" and not release_matches():
        if current == state["guest"]:
            raise ValueError("版本已变化但仍在使用旧修复库，请先核对新版软件包。")
        desktop_action(account, "disable")
        shutil.rmtree(ROOT)
        print("已清理 release 49 修复配置，保留当前版本的输入库。")
        return
    if current not in (FACTORY_SHA, state["guest"]):
        raise ValueError("现有系统输入库已变化，需核对后恢复。")
    if action == "rollback":
        desktop_action(account, "disable")
        if current != FACTORY_SHA:
            swap_receiver(ROOT / "factory.so")
        shutil.rmtree(ROOT)
        print("已恢复系统输入库和先前的启动配置，重启安卓容器后生效。")
        return
    if state["guest"] != release[GUEST]["sha256"] or digest(ROOT / GUEST) != state["guest"]:
        raise ValueError("已有输入库不同，请先回退。")
    host_sha = digest(ROOT / HOST)
    if host_sha != release[HOST]["sha256"]:
        if host_sha != PREVIOUS_PRESENTER_SHA:
            raise ValueError("已有窗口程序不同，请先核对。")
        # The Android receiver and its factory backup are identical across these builds.
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            unpack(Path(folder), HOST, release[HOST])
            os.replace(Path(folder) / HOST, ROOT / HOST)
    # If interrupted between the two changes, running apply or rollback resumes.
    if current == FACTORY_SHA:
        swap_receiver(ROOT / GUEST)
    try:
        desktop_action(account, "enable")
    except Exception:
        if current == FACTORY_SHA:
            swap_receiver(ROOT / "factory.so")
            desktop_action(account, "disable")
        raise
    print("已安装 Android 原生手写笔支持。保存安卓工作后，以桌面账号执行 droidloomctl restart。")


def main(action):
    if action == "launch":
        try:
            active = release_matches() and digest(LIVE) == json.loads((ROOT / "state.json").read_text())["guest"]
        except (OSError, ValueError, KeyError):
            active = False
        binary = ROOT / HOST if active else STOCK
        os.execv(binary, [str(binary)])
    if action in ("enable", "disable"):
        configure(action)
        return
    if action not in ("check", "apply", "rollback"):
        raise ValueError("支持 check、apply 和 rollback。")
    if (action != "rollback" and not release_matches()) or LIVE.resolve() != LIVE or Path("/usr/lib/droidloom/current/lib64/libinputflinger.so").resolve() != LIVE:
        raise ValueError("此原生笔版本仅适用于标准 Droidloom 0.1.0 release 49。")
    release = json.loads((ASSETS / "payload.json").read_bytes())
    if action != "rollback":
        check_guest_binding()
    if action == "check":
        current = digest(LIVE)
        if current not in (FACTORY_SHA, release[GUEST]["sha256"]):
            raise ValueError("现有输入库与支持的原版及修复版均不匹配。")
        print("已识别原生笔输入库。" if current != FACTORY_SHA else "已识别 release 49 原版安卓输入库。")
        return
    uid = int(os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or "0")
    if os.geteuid() != 0 or uid < 1000:
        raise ValueError("请从当前桌面账号通过修复助手或 sudo 运行。")
    with open("/run/lock/tb710fu-droidloom-r49.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action != "rollback" and not release_matches():
            raise ValueError("Droidloom 在等待期间已升级。")
        apply_or_rollback(pwd.getpwuid(uid), action, release)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (KeyError, ValueError, OSError, EOFError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
