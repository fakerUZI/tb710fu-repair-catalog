"""Select a compatible Droidloom presenter and install a per-user override."""

import argparse
from contextlib import suppress
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ASSETS = Path(__file__).resolve().parent
STOCK = Path("/usr/bin/droidloom-wayland")
PACKAGE = Path("/usr/share/droidloom/package.json")
INPUT_FLINGER = Path("/usr/lib/droidloom/current/lib64/libinputflinger.so")
UNIT = "droidloom.service"
FOLDER = "tb710fu-droidloom-pen-compat"
PRESENTER = "droidloom-wayland"
PRESENTER_R42 = "droidloom-wayland-r42"
LAUNCHER = "pen-compat.py"
LEGACY_LAUNCHER_SHA = "d5c30ccdcaf38eaba1641a8ad5036777f5aa1ae5fdde2c6fdec51a83c689d566"
OVERRIDE = (
    "# Managed by the TB710FU Droidloom pen compatibility test.\n"
    "[Service]\n"
    "ExecStart=\n"
    f"ExecStart=/usr/bin/python3 -IB %h/.local/libexec/{FOLDER}/{LAUNCHER} run\n"
).encode()


def package_info():
    try:
        package = json.loads(PACKAGE.read_text())
        return package if isinstance(package, dict) else {}
    except (OSError, ValueError):
        return {}


def compatible_presenter(package):
    release = package.get("release")
    if package.get("version") != "0.1.0" or package.get("architecture") != "aarch64":
        return None
    if type(release) is not int or not 42 <= release <= 49:
        return None
    name, abi = (PRESENTER_R42, 7) if release == 42 else (PRESENTER, 8)
    try:
        library = INPUT_FLINGER.read_bytes()
    except OSError:
        return None
    if f"DROIDLOOM_INPUT_ABI={abi:05d};".encode() not in library:
        return None
    if b"DROIDLOOM_NATIVE_INPUT_ABI=2;" not in library:
        return None
    return name


def systemctl(*args):
    return subprocess.run(
        ["/usr/bin/systemctl", "--user", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def require_launch(command):
    active = systemctl("show", UNIT, "--property=ExecStart", "--value")
    if f"argv[]={command} ;" not in active:
        raise ValueError("无法确认 Droidloom 的启动命令，请检查其他启动覆盖。")


def verify_platform():
    if platform.machine() != "aarch64" or b"TB710FU" not in Path(
        "/proc/device-tree/model"
    ).read_bytes():
        raise ValueError("该测试版仅适用于 TB710FU 的 ARM64 Ubuntu。")
    package = package_info()
    release = package.get("release")
    if not compatible_presenter(package) or not os.access(STOCK, os.X_OK):
        current = f"release {release}" if type(release) is int else "未检测到发行版"
        raise ValueError(f"当前 Droidloom 为 {current}。测试包需要 ARM64 release 42–49 及匹配的安卓输入 ABI。")
    if 'Name="NVTCapacitivePen"' not in Path("/proc/bus/input/devices").read_text():
        raise ValueError("Ubuntu 尚未识别本机手写笔，请先验证系统内的手写笔。")


def replace(path, contents, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(contents)
        os.fchmod(temporary.fileno(), mode)
    try:
        os.replace(temporary.name, path)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def same_file(path, contents):
    return path.is_file() and not path.is_symlink() and path.read_bytes() == contents


def legacy_files(destination, files):
    launcher = destination / LAUNCHER
    if not launcher.is_file() or launcher.is_symlink():
        return False
    if hashlib.sha256(launcher.read_bytes()).hexdigest() != LEGACY_LAUNCHER_SHA:
        return False
    if not same_file(destination / PRESENTER, files[PRESENTER][0]):
        return False
    names = {path.name for path in destination.iterdir()}
    return names == {LAUNCHER, PRESENTER} or (
        names == set(files) and same_file(destination / PRESENTER_R42, files[PRESENTER_R42][0])
    )


def install_files(destination, files):
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=f".{FOLDER}-"))
    try:
        for name, (contents, mode) in files.items():
            path = staging / name
            path.write_bytes(contents)
            path.chmod(mode)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["apply", "check", "rollback", "run"])
    action = parser.parse_args().action
    if os.geteuid() == 0:
        raise ValueError("请用当前 GNOME 桌面账号运行。")
    home = Path.home()
    destination = home / ".local/libexec" / FOLDER
    if action == "run":
        name = compatible_presenter(package_info())
        candidate = destination / name if name else STOCK
        executable = candidate if os.access(candidate, os.X_OK) else STOCK
        os.execv(executable, [str(executable)])

    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    dropin = config / "systemd/user/droidloom.service.d/90-tb710fu-pen-compat.conf"
    files = {
        PRESENTER: ((ASSETS / PRESENTER).read_bytes(), 0o755),
        PRESENTER_R42: ((ASSETS / PRESENTER_R42).read_bytes(), 0o755),
        LAUNCHER: ((ASSETS / LAUNCHER).read_bytes(), 0o644),
    }
    lock_path = Path(os.environ["XDG_RUNTIME_DIR"]) / f"{FOLDER}.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        enabled = same_file(dropin, OVERRIDE)
        if dropin.is_symlink() or (dropin.exists() and not enabled):
            raise ValueError("发现不同的 Droidloom 覆盖配置，已保留原文件。")
        installed = destination.is_dir() and not destination.is_symlink() and all(
            same_file(destination / name, contents)
            for name, (contents, _) in files.items()
        )
        legacy = destination.is_dir() and not destination.is_symlink() and legacy_files(destination, files)

        if action == "rollback":
            if enabled:
                dropin.unlink()
                try:
                    systemctl("daemon-reload")
                except subprocess.CalledProcessError:
                    replace(dropin, OVERRIDE)
                    raise
            if legacy or (installed and {path.name for path in destination.iterdir()} == set(files)):
                for name in files:
                    (destination / name).unlink(missing_ok=True)
                destination.rmdir()
            print("已撤销本测试版覆盖。下次启动 Droidloom 时恢复原版。")
            return

        verify_platform()
        candidate_command = f"/usr/bin/python3 -IB {destination / LAUNCHER} run"
        require_launch(candidate_command if enabled else STOCK)
        if destination.is_symlink() or (destination.exists() and not (installed or legacy)):
            raise ValueError("测试版路径有其他文件，已保留原件。")

        if action == "check":
            print("Droidloom 手写笔触控兼容测试版已安装。" if enabled and installed
                  else "本机符合 Droidloom 手写笔测试版条件。")
            return
        if legacy:
            replace(destination / PRESENTER_R42, *files[PRESENTER_R42])
            replace(destination / LAUNCHER, *files[LAUNCHER])
        elif not installed:
            install_files(destination, files)
        if not enabled:
            replace(dropin, OVERRIDE)
            try:
                systemctl("daemon-reload")
                require_launch(candidate_command)
            except (ValueError, subprocess.CalledProcessError):
                dropin.unlink()
                with suppress(subprocess.CalledProcessError):
                    systemctl("daemon-reload")
                raise
        print("已安装。保存安卓应用中的工作后，重启 Droidloom 即可测试。")
        print("下笔按触控输入传递。笔记应用若提供「手指书写」选项，请开启。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
