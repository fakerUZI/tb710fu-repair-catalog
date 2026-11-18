"""Install the reviewed ARM64 release and apply TB710FU game compatibility."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import time

RELEASE = {"architecture": "aarch64", "version": "0.1.0", "release": 42}
PACKAGES = {
    "droidloom-runtime-0.1.0-42-aarch64.pkg.tar.zst": (10844199, "67d0f065230d21fde0f1ed181448624dca4bc3de5868c727d30c30b6e0bbc955"),
    "droidloom-image-0.1.0-42-aarch64.pkg.tar.zst": (969606997, "8bd61f9a65b6fc829b430b6872ccd9931489b8d7c87c0a11d6d1a73bf6f94326"),
}
UPSTREAM = "https://github.com/denialwm/droidloom/releases/download/packages/"
UTIL_LINUX = "util-linux-2.42.4"
UTIL_SHA = "fbd62a100ab7bb8746ba0661255c3c48185b1e9021507c624da01fbc696330ec"
CACHE = Path("/var/cache/tb710fu-droidloom-r42")
BACKUP = Path("/var/backups/tb710fu-droidloom-r42-repair")
STATE = BACKUP / "state.json"
SPEC = Path("/etc/droidloom/cell.json")
VERSION = Path("/usr/share/droidloom/package.json")
CTL = Path("/usr/bin/droidloomctl")
HELPER = Path("/usr/lib/droidloom/droidloom-package-helper")
UNSHARE = Path("/usr/local/libexec/droidloom/unshare")
DROPINS = Path("/etc/systemd/system/droidloomd.service.d")
LAYOUT = DROPINS / "30-aarch64-legacy-mmap.conf"
COMPAT = DROPINS / "10-ubuntu-compat.conf"
MEDIA = Path("/etc/modules-load.d/droidloom-media.conf")
GENERATED = [SPEC, Path("/etc/droidloom/runtime.env"),
             Path("/etc/droidloom/package-state.json"),
             Path("/etc/polkit-1/rules.d/49-droidloom.rules")]
DNS = {
    "captive_portal_https_url": "https://connect.rom.miui.com/generate_204",
    "captive_portal_http_url": "http://connect.rom.miui.com/generate_204",
}
PACKAGE = "com.shenlan.m.reverse1999"
ENTRY = PACKAGE + "/com.ssgame.mobile.gamesdk.frame.AppStartUpActivity"


def run(*args, capture=False, check=True):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE if capture else None,
                          check=check)


def read(path):
    return json.loads(path.read_text()) if path.is_file() else None


def save(path, data):
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)


def host_check():
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    model_file = Path("/sys/firmware/devicetree/base/model")
    model = model_file.read_text().rstrip("\0") if model_file.is_file() else ""
    if os.uname().machine != "aarch64" or release.get("ID") != "ubuntu" or "TB710FU" not in model:
        raise RuntimeError("仅适用于 Lenovo TB710FU Ubuntu ARM64")
    if release.get("VERSION_ID", "").strip('"') != "26.04":
        raise RuntimeError("此包仅验证 Ubuntu 26.04")


def package_state():
    version = read(VERSION)
    if version == RELEASE and CTL.is_file():
        return 42
    if version == dict(RELEASE, release=28) and CTL.is_file():
        return 28
    if version is None and not CTL.exists() and not SPEC.exists():
        return None
    raise RuntimeError("发现未知或不完整的 Droidloom 安装，停止以保留现有运行时")


def require_desktop():
    uid = int(os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or "0")
    if uid < 1000:
        raise RuntimeError("请由当前桌面普通用户通过修复助手、sudo 或 pkexec 运行")
    account = pwd.getpwuid(uid)
    runtime = Path("/run/user") / str(uid)
    command = ("runuser", "-u", account.pw_name, "--", "env",
               f"HOME={account.pw_dir}", f"XDG_RUNTIME_DIR={runtime}",
               f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime / 'bus'}")
    environment = run(*command, "systemctl", "--user", "show-environment", capture=True).stdout
    wayland = next((line.split("=", 1)[1] for line in environment.splitlines()
                    if line.startswith("WAYLAND_DISPLAY=")), "")
    if not wayland or not (runtime / wayland).is_socket():
        raise RuntimeError("需要当前用户已登录 Wayland 桌面")
    return uid, command


def check_host_config():
    mapping = [line.split() for line in Path("/proc/self/uid_map").read_text().splitlines()]
    if mapping != [["0", "0", "4294967295"]]:
        raise RuntimeError("需要通过宿主 Ubuntu 的 sudo 或 pkexec 运行，当前 root 位于用户命名空间")
    for path in (SPEC.parent, SPEC):
        if not exists(path):
            continue
        info = path.lstat()
        expected_type = stat.S_ISDIR if path == SPEC.parent else stat.S_ISREG
        if not expected_type(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(f"宿主配置权限异常：{path}。请核对其来源、属主和权限，保留原文件。")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url, size, sha256):
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = CACHE / url.rsplit("/", 1)[1]
    if target.is_file() and target.stat().st_size == size and digest(target) == sha256:
        return target
    partial = target.with_name(target.name + ".part")
    if partial.is_file() and partial.stat().st_size >= size:
        if partial.stat().st_size == size and digest(partial) == sha256:
            partial.replace(target)
            return target
        partial.unlink()
    command = ("curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
               "--connect-timeout", "20", "--retry", "3", "--output", partial)
    result = run(*command, "--continue-at", "-", url, check=False)
    if result.returncode == 33:
        run(*command, url)
    else:
        result.check_returncode()
    if partial.stat().st_size != size or digest(partial) != sha256:
        partial.unlink()
        raise RuntimeError(f"官方下载文件 SHA-256 校验失败：{target.name}")
    partial.replace(target)
    return target


def apt(*packages):
    run("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y",
        "--no-install-recommends", "--no-upgrade", "install", *packages)


def supports_unshare(path):
    return path.is_file() and "--forward-signals" in run(
        path, "--help", capture=True, check=False).stdout


def build_unshare(stage):
    if supports_unshare(UNSHARE):
        return
    if exists(UNSHARE):
        raise RuntimeError(f"保留未知的专用 unshare：{UNSHARE}")
    if supports_unshare(Path("/usr/bin/unshare")):
        return
    if not shutil.which("cc") or not shutil.which("make") or not Path("/usr/include/stdio.h").is_file():
        apt("gcc", "make", "libc6-dev")
    source = fetch("https://www.kernel.org/pub/linux/utils/util-linux/v2.42/" + UTIL_LINUX + ".tar.xz", 10729748, UTIL_SHA)
    run("tar", "-xf", source, "-C", stage)
    directory = stage / UTIL_LINUX
    subprocess.run(["./configure", "--disable-all-programs", "--enable-unshare",
                    "--disable-nls", "--without-systemd", "--without-udev"],
                   cwd=directory, check=True)
    subprocess.run(["make", "-j2", "unshare"], cwd=directory, check=True)
    if not supports_unshare(directory / "unshare"):
        raise RuntimeError("构建结果缺少 --forward-signals")
    output = stage / UNSHARE.relative_to("/")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(directory / "unshare", output)


def staged_runtime(stage):
    for name, (size, checksum) in PACKAGES.items():
        archive = fetch(UPSTREAM + name, size, checksum)
        run("tar", "--use-compress-program=zstd", "--no-same-owner", "-xf",
            archive, "-C", stage, "usr")
    if read(stage / VERSION.relative_to("/")) != RELEASE:
        raise RuntimeError("安装包版本元数据不匹配")
    binaries = sorted((stage / "usr/bin").glob("droidloom*"))
    for binary in [*binaries, stage / HELPER.relative_to("/")]:
        result = run("ldd", binary, capture=True)
        if "not found" in result.stdout:
            raise RuntimeError(f"{binary.name} 缺少动态库：\n{result.stdout}")
    if not (stage / "usr/lib/droidloom/images/images/vendor.raw.img").is_file():
        raise RuntimeError("缺少 Android vendor 镜像")
    paths = [Path("/usr/lib/droidloom"), Path("/usr/share/droidloom")]
    for pattern in ("usr/bin/droidloom*", "usr/lib/systemd/system/droidloom*",
                    "usr/lib/systemd/user/droidloom*", "usr/share/licenses/droidloom-*",
                    "usr/share/doc/droidloom",
                    "usr/share/polkit-1/actions/org.droidloom.setup.policy"):
        paths.extend(Path("/") / item.relative_to(stage) for item in sorted(stage.glob(pattern)))
    return paths


def staged_text(stage, path, data):
    target = stage / path.relative_to("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data)


def exists(path):
    return path.exists() or path.is_symlink()


def erase(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif exists(path):
        path.unlink()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    run("cp", "-a", "--", source, target)


def snapshot(paths, state):
    BACKUP.mkdir(mode=0o700)
    try:
        for path in paths:
            if exists(path):
                copy(path, BACKUP / "files" / path.relative_to("/"))
        state["paths"] = [str(path) for path in paths]
        save(STATE, state)
    except Exception:
        shutil.rmtree(BACKUP)
        raise


def archive_rolled_back(uid):
    if not exists(BACKUP):
        return
    info = BACKUP.lstat()
    receipt = BACKUP / "rolled-back.json"
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
            or STATE.exists() or not receipt.is_file() or receipt.is_symlink()
            or (read(receipt) or {}).get("uid") != uid):
        raise RuntimeError(f"发现未确认的本机备份，保留原目录：{BACKUP}")
    archived = BACKUP.with_name(f"{BACKUP.name}-rolled-back-{time.time_ns()}")
    BACKUP.rename(archived)
    print(f"上次已回退的快照保留于 {archived}", flush=True)


def unit_running(*command):
    return run(*command, "is-active", "--quiet", "droidloom.service",
               check=False).returncode == 0


def stop(user):
    # Use the desktop service for a graceful Android shutdown before replacing files.
    if unit_running(*user, "systemctl", "--user"):
        run(*user, "systemctl", "--user", "stop", "droidloom.service")
    if Path("/usr/lib/systemd/system/droidloomd.service").exists():
        run("systemctl", "stop", "droidloomd.service")


def reload_units(user):
    run("systemctl", "daemon-reload")
    run(*user, "systemctl", "--user", "daemon-reload")


def connect_android(uid):
    namespace = (Path("/run/netns") / f"droidloom-u{uid}").stat()
    candidates = run("pgrep", "-f", "^/init second_stage( |$)", capture=True).stdout.split()
    for pid in candidates:
        net = Path(f"/proc/{pid}/ns/net")
        try:
            current = net.stat()
        except FileNotFoundError:
            continue
        if (current.st_dev, current.st_ino) == (namespace.st_dev, namespace.st_ino):
            return ("nsenter", "--target", pid, "--all", "--root", "--wd", "--")
    raise RuntimeError("找不到所属桌面用户的 Droidloom Android init")


def android_call(android, tool, *args, capture=False):
    return run(*android, "/system/bin/" + tool, *args, capture=capture)


def global_settings(android):
    return {key: android_call(android, "settings", "get", "global", key, capture=True).stdout.strip()
            for key in DNS}


def wifi_validated(android):
    lines = android_call(android, "dumpsys", "connectivity", capture=True).stdout.splitlines()
    return any("NetworkAgentInfo{network" in line and "WIFI CONNECTED" in line
               and "VALIDATED" in line for line in lines)


def connect_wifi(android):
    last_error = ""
    for attempt in range(5):
        if not wifi_validated(android):
            if attempt == 0:
                subprocess.run([*android, "/system/bin/cmd", "wifi", "set-wifi-enabled", "enabled"],
                               check=False, capture_output=True, timeout=15)
            result = subprocess.run([*android, "/system/bin/cmd", "wifi", "connect-network", "Droidloom", "open"],
                                    check=False, capture_output=True, text=True, timeout=15)
            if result.returncode:
                details = (result.stderr.strip() or result.stdout.strip())[:500]
                last_error = f"（连接命令退出码 {result.returncode} {details}）"
        for _ in range(5):
            if wifi_validated(android):
                print("Droidloom 虚拟 Wi-Fi 已连接并通过联网校验", flush=True)
                return
            time.sleep(2)
    raise RuntimeError("Droidloom 虚拟 Wi-Fi 未通过真实联网校验" + last_error)


def configure_network(uid, state):
    android = connect_android(uid)
    previous = global_settings(android)
    if state["same_data"] and "dns_before" not in state:
        state["dns_before"] = previous
        save(STATE, state)
    for key, value in DNS.items():
        if previous[key] == "null":
            android_call(android, "settings", "put", "global", key, value)
    connect_wifi(android)


def restore_network(uid, state, user):
    if "dns_before" not in state:
        return
    if not unit_running(*user, "systemctl", "--user"):
        run(*user, CTL, "start")
    android = connect_android(uid)
    for key, value in state["dns_before"].items():
        if global_settings(android)[key] != DNS[key] or value == DNS[key]:
            continue
        if value == "null":
            android_call(android, "settings", "delete", "global", key)
        else:
            android_call(android, "settings", "put", "global", key, value)


def rollback(uid, user, state):
    if state["uid"] != uid:
        raise RuntimeError("备份属于另一桌面用户")
    current = read(VERSION)
    if state.get("complete") and current != RELEASE:
        raise RuntimeError("Droidloom 已被更换，停止回退以保留当前版本")
    restore_network(uid, state, user)
    stop(user)
    for name in reversed(state["paths"]):
        path = Path(name)
        old = BACKUP / "files" / path.relative_to("/")
        erase(path)
        if exists(old):
            copy(old, path)
    reload_units(user)
    if state["running"] and CTL.is_file():
        run(*user, CTL, "start")
    save(BACKUP / "rolled-back.json", state)
    STATE.unlink()
    print(f"软件与设置已回退，Android 数据和本机备份保留于 {BACKUP}", flush=True)


def apply(uid, user):
    release = package_state()
    previous = read(SPEC)
    if previous and previous.get("host_uid") != uid:
        raise RuntimeError("现有 Android 数据属于另一桌面用户")
    if read(STATE):
        state = read(STATE)
        if not state.get("complete") or state["uid"] != uid or release != 42:
            raise RuntimeError("已有未完成或不兼容的本机备份，请先检查或回退")
        run(*user, CTL, "start")
        configure_network(uid, state)
        return
    archive_rolled_back(uid)
    if release != 42 and run("dpkg-query", "-S", CTL, capture=True,
                             check=False).returncode == 0:
        raise RuntimeError("现有 Droidloom 由 dpkg 管理，请使用原软件包管理方式")
    needed = [package for command, package in (("curl", "curl"), ("zstd", "zstd"),
              ("ip", "iproute2"), ("nft", "nftables"), ("iptables", "iptables"),
              ("pkexec", "pkexec")) if not shutil.which(command)]
    if needed:
        apt(*needed)
    if release != 42:
        apt("libvulkan1", "libgbm1", "libdrm2", "libpipewire-0.3-0t64",
            "libwayland-client0", "libwayland-server0", "libxkbcommon0", "libegl1")
    running = unit_running(*user, "systemctl", "--user")
    state = {"uid": uid, "release": release, "running": running, "complete": False,
             "same_data": release == 42 and previous is not None}
    with tempfile.TemporaryDirectory(prefix="tb710fu-droidloom-", dir="/var/tmp") as workspace:
        stage = Path(workspace)
        build_unshare(stage)
        files = staged_runtime(stage) if release != 42 else []
        if (stage / UNSHARE.relative_to("/")).is_file():
            files.append(UNSHARE)
        compat = ("[Service]\nEnvironment=PATH=/usr/local/libexec/droidloom:"
                  "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n")
        layout = ("# TB710FU: keep default mmap addresses within the legacy LuaJIT range.\n"
                  "[Service]\nExecStart=\n"
                  "ExecStart=/usr/bin/setarch --addr-compat-layout /usr/bin/droidloomd\n")
        if supports_unshare(UNSHARE) or UNSHARE in files:
            if not COMPAT.exists():
                staged_text(stage, COMPAT, compat)
                files.append(COMPAT)
            elif "PATH=/usr/local/libexec/droidloom:" not in COMPAT.read_text():
                raise RuntimeError(f"现有服务设置未引用专用 unshare：{COMPAT}")
        if not LAYOUT.exists():
            staged_text(stage, LAYOUT, layout)
            files.append(LAYOUT)
        elif "ExecStart=/usr/bin/setarch --addr-compat-layout /usr/bin/droidloomd" not in LAYOUT.read_text():
            raise RuntimeError(f"保留其他内存布局设置：{LAYOUT}")
        if not Path("/dev/dma_heap/system").is_char_device():
            run("modprobe", "system_heap")
        if not Path("/dev/dma_heap/system").is_char_device():
            raise RuntimeError("当前内核缺少 system DMA heap")
        if not MEDIA.exists():
            staged_text(stage, MEDIA, "system_heap\n")
            files.append(MEDIA)
        data = f"/var/lib/droidloom/users/{uid}/host-storage-tb710fu-r42-{time.time_ns()}"
        if release == 28 and not previous:
            raise RuntimeError("旧版配置缺失")
        stop(user)
        try:
            snapshot(files + (GENERATED if not state["same_data"] else []), state)
        except Exception:
            if running:
                run(*user, CTL, "start", check=False)
            raise
        for destination in files:
            erase(destination)
            copy(stage / destination.relative_to("/"), destination)
        if release == 28:
            previous["data_dir"] = data
            save(SPEC, previous)
        if previous is None:
            SPEC.parent.mkdir(parents=True, exist_ok=True)
            save(SPEC, {"host_uid": uid, "data_dir": data})
            SPEC.chmod(0o644)
            run(HELPER, "setup", "--uid", str(uid))
        elif release != 42:
            run(HELPER, "after-install")
        reload_units(user)
        run(*user, CTL, "start")
        configure_network(uid, state)
        state["complete"] = True
        save(STATE, state)
    print("Droidloom 42 已就绪。此修复未修改或安装任何游戏 APK。", flush=True)
    print("已安装游戏可从桌面启动，或由桌面用户运行：", flush=True)
    print(f"droidloomctl launch {PACKAGE} --component {ENTRY}", flush=True)


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else "check"
    if action not in ("check", "apply", "rollback"):
        raise RuntimeError("用法：repair.py check|apply|rollback")
    host_check()
    if action == "check":
        release = package_state()
        print(f"Droidloom release：{release if release is not None else '未安装，应用时下载官方包'}")
        print(f"兼容内存布局：{'已设定' if LAYOUT.is_file() else '待配置'}")
        print(f"本机回退快照：{'存在' if STATE.is_file() else '无'}")
        return
    if os.geteuid() != 0:
        raise RuntimeError("安装和回退需要管理员权限")
    check_host_config()
    uid, user = require_desktop()
    with open("/run/lock/tb710fu-droidloom-r42-repair.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "rollback":
            state = read(STATE)
            if not state:
                raise RuntimeError("本机没有可回退的修复状态")
            rollback(uid, user, state)
        else:
            try:
                apply(uid, user)
            except Exception:
                state = read(STATE)
                if state and not state.get("complete"):
                    print("安装中断，正在尝试恢复本机快照", file=sys.stderr)
                    try:
                        rollback(uid, user, state)
                    except Exception as error:
                        print(f"自动回退中断：{error}。备份仍在 {BACKUP}", file=sys.stderr)
                raise


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Droidloom 修复失败：{error}", file=sys.stderr)
        sys.exit(1)
