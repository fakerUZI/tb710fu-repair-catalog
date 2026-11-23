#!/usr/bin/env python3
"""Install and supervise the TB710FU magnetic keyboard service."""

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import platform
import pwd
import shutil
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory

import pogo_trial as pogo
from gnome_shortcuts import GnomeShortcuts


KERNEL = "7.2.6-tb710fu-20261118"
PREFIX = Path("/usr/local/lib/tb710fu-pogo")
UNIT = "tb710fu-pogo.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT
RUNTIME = Path("/run/tb710fu-pogo")
DTBO = RUNTIME / pogo.FIRMWARE.name
STATE = Path("/var/lib/tb710fu-pogo")
MARKER = ".managed-by-tb710fu-repair-assistant"
PAYLOAD = (
    "pogo_install.py", "pogo_trial.py", "build_overlay.py", "gnome_shortcuts.py",
    *pogo.PREPARED_SOURCES, *(name.replace(".dts", ".dtbo") for name in pogo.PREPARED_SOURCES),
    *(f"driver/{name}.ko" for name in pogo.MODULES), UNIT,
)


@contextmanager
def lock(name):
    path = f"/run/lock/{name}.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def systemctl(*arguments, check=True):
    return subprocess.run(
        ["/usr/bin/systemctl", *arguments, UNIT], check=check, timeout=55,
        stdout=subprocess.DEVNULL if not check else None,
    ).returncode == 0


def owned_link(path, destination):
    if not os.path.lexists(path):
        return False
    if not path.is_symlink() or os.readlink(path) != str(destination):
        raise RuntimeError(f"已存在其他程序创建的 {path}，请先核对来源")
    return True


def installed_payload():
    if not os.path.lexists(PREFIX):
        return False
    if PREFIX.is_symlink() or not (PREFIX / MARKER).is_file():
        raise RuntimeError(f"无法确认 {PREFIX} 的来源")
    return True


def installed_unit():
    if not os.path.lexists(UNIT_PATH):
        return False
    if (not installed_payload() or UNIT_PATH.is_symlink() or not UNIT_PATH.is_file()
            or UNIT_PATH.read_bytes() != (PREFIX / UNIT).read_bytes()):
        raise RuntimeError(f"无法确认 {UNIT_PATH} 的来源")
    return True


def publish_unit(source):
    with TemporaryDirectory(prefix=".tb710fu-pogo-", dir=UNIT_PATH.parent) as work:
        temporary = Path(work) / UNIT
        shutil.copyfile(source, temporary)
        temporary.chmod(0o644)
        temporary.replace(UNIT_PATH)


def reload_units():
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True, timeout=15)


def activate():
    systemctl("start")
    if not systemctl("is-active", check=False):
        raise RuntimeError("磁吸键盘服务未进入运行状态")
    systemctl("enable")


def check_install():
    if platform.release() != KERNEL:
        raise RuntimeError(f"此驱动适用于 {KERNEL}，当前内核为 {platform.release()}")
    owned_link(pogo.FIRMWARE, DTBO)
    previous = installed_payload()
    if installed_unit() and previous and systemctl("is-active", check=False):
        if pogo.KEYBOARD_READY.read_text().strip() != "1":
            raise RuntimeError("服务仍在运行，但键盘串口尚未就绪")
        return
    pogo.Trial(firmware=DTBO).check(pogo.TREE)


def notify_ready():
    address = os.environ["NOTIFY_SOCKET"]
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.sendto(b"READY=1\nSTATUS=Pogo UART ready", address)


def serve():
    if not owned_link(pogo.FIRMWARE, DTBO):
        raise RuntimeError("磁吸键盘设备树固件入口缺失")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with lock("tb710fu-pogo-trial"):
        trial = pogo.Trial(firmware=DTBO)
        dts = trial.check(pogo.TREE)
        try:
            trial.start(dts)
            notify_ready()
            while True:
                signal.pause()
        finally:
            trial.stop()
            print("磁吸键盘服务已释放模块并恢复 gpio-keys。", flush=True)


def setup_shortcuts():
    shortcuts = GnomeShortcuts.active()
    if shortcuts is None:
        return
    shortcuts.restore()
    shortcuts.state = STATE / shortcuts.state.name
    if shortcuts.state.is_file():
        return
    try:
        shortcuts.apply()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        shortcuts.restore()
        raise


def restore_shortcuts():
    for state in sorted(STATE.glob("shortcuts-*.json")):
        uid = int(state.stem.removeprefix("shortcuts-"))
        try:
            account = pwd.getpwuid(uid)
        except KeyError:
            state.unlink()
            continue
        if (Path("/run/user") / str(uid) / "bus").is_socket():
            shortcuts = GnomeShortcuts(account)
            shortcuts.state = state
            shortcuts.restore()
        else:
            with TemporaryDirectory(prefix="tb710fu-pogo-dconf-", dir="/run") as run:
                os.chown(run, uid, account.pw_gid)
                shortcuts = GnomeShortcuts(account, private_runtime=run)
                shortcuts.state = state
                shortcuts.restore()


def apply():
    check_install()
    previous = installed_payload()
    had_unit = installed_unit()
    had_firmware = owned_link(pogo.FIRMWARE, DTBO)
    was_active = had_unit and systemctl("is-active", check=False)

    if previous and had_unit and had_firmware and all(
            (PREFIX / name).is_file() and (PREFIX / name).read_bytes() == (pogo.ROOT / name).read_bytes()
            for name in PAYLOAD):
        activate()
    else:
        PREFIX.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".tb710fu-pogo-", dir=PREFIX.parent) as work:
            backup = Path(work) / "previous"
            staging = Path(work) / "new"
            for name in PAYLOAD:
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(pogo.ROOT / name, target)
                target.chmod(0o644)
            (staging / MARKER).touch(mode=0o644)

            if was_active:
                systemctl("stop")
            if previous:
                PREFIX.rename(backup)
            created_firmware = False
            published_unit = False
            try:
                staging.rename(PREFIX)
                if not had_firmware:
                    pogo.FIRMWARE.parent.mkdir(parents=True, exist_ok=True)
                    pogo.FIRMWARE.symlink_to(DTBO)
                    created_firmware = True
                publish_unit(PREFIX / UNIT)
                published_unit = True
                reload_units()
                activate()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                if published_unit:
                    systemctl("stop", check=False)
                    if not had_unit:
                        systemctl("disable", check=False)
                        UNIT_PATH.unlink(missing_ok=True)
                if created_firmware:
                    pogo.FIRMWARE.unlink()
                if PREFIX.exists():
                    PREFIX.rename(staging)
                if backup.exists():
                    backup.rename(PREFIX)
                if had_unit and published_unit:
                    publish_unit(PREFIX / UNIT)
                reload_units()
                if was_active:
                    systemctl("start")
                raise

    try:
        setup_shortcuts()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"GNOME 窗口快捷键未配置：{error}", flush=True)
    print("磁吸键盘服务已启动并设为开机自动加载，即刻生效，无需重启。", flush=True)


def rollback():
    previous = installed_payload()
    had_unit = installed_unit()
    had_firmware = owned_link(pogo.FIRMWARE, DTBO)
    if had_unit:
        systemctl("stop")
        systemctl("disable")
    restore_shortcuts()
    if had_unit:
        UNIT_PATH.unlink(missing_ok=True)
        reload_units()
    if had_firmware:
        pogo.FIRMWARE.unlink()
    if previous:
        shutil.rmtree(PREFIX)
    print("磁吸键盘服务及其快捷键已卸载，无需重启。", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "rollback", "serve"))
    action = parser.parse_args().action
    if action == "check":
        check_install()
        print("当前内核、设备树和磁吸键盘模块检查通过。")
        return
    if os.geteuid() != 0:
        raise PermissionError("磁吸键盘安装需要管理员权限")
    if action == "serve":
        serve()
        return
    with lock("tb710fu-pogo-install"):
        (apply if action == "apply" else rollback)()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
