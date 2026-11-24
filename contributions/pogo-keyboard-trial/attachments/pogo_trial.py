#!/usr/bin/env python3
"""Supervised TB710FU Pogo trial that leaves the built-in keys bound."""

import argparse
import fcntl
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from build_overlay import compile_dtbo, make_dts
from gnome_shortcuts import GnomeShortcuts


ROOT = Path(__file__).resolve().parent
TREE = Path("/sys/firmware/devicetree/base")
PLATFORM_DEVICES = Path("/sys/bus/platform/devices")
GPIO_DRIVER = Path("/sys/bus/platform/drivers/gpio-keys")
KEYBOARD_READY = Path("/sys/bus/platform/devices/lenovo,lenovo_keyboard/transport_ready")
INPUTS = Path("/sys/class/input")
FIRMWARE = Path("/usr/lib/firmware/tb710fu/pogo-keyboard.dtbo")
PMIC = TREE / "soc@0/rsc@17a00000/regulators-0"
MODULES = ("tb_pogo_overlay", "lenovo_keyboard")
PREPARED_SOURCES = ("pogo-uart-only.dts",)
OLD_MODULES = ("uartov", "rpmhmain", "voltset")
EXPECTED_INPUTS = {
    "Lenovo Keyboard Pack For Yoga Tab Keyboard",
    "Lenovo Keyboard Pack For Yoga Tab Touchpad",
}
MODULE_LOG_PATTERN = (
    r"OF: (overlay|resolver)|tb_pogo_overlay|lenovo_keyboard|pogo-keyboard\.dtbo"
    r"|[Uu]nknown symbol|version magic|disagrees about version"
    r"|module verification|[Ii]nvalid module format"
)
PROBE_LOG_PATTERN = MODULE_LOG_PATTERN + r"|gpio-keys|a9c000|Pogo supply|\[lenovo_KB-err\]"
DEFERRED_DEVICES = Path("/sys/kernel/debug/devices_deferred")


def kernel_logs(started, pattern, limit):
    try:
        result = subprocess.run(
            [
                "journalctl", "-k", "-b", "--since", f"@{int(started)}",
                "--grep", pattern, "--lines", str(limit),
                "--no-pager", "--quiet", "--output", "cat",
            ],
            capture_output=True, encoding="utf-8", errors="replace", timeout=4,
        )
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def report_module_failure(module, started):
    logs = kernel_logs(started, MODULE_LOG_PATTERN, 20)
    print(f"{module} 加载失败，相关内核日志", flush=True)
    print(logs or "本次加载没有读到匹配的内核日志。", flush=True)


def check_supplies(pmic):
    if (pmic / "qcom,pmic-id").read_bytes() != b"b\0":
        raise RuntimeError("找不到已核对的 PM8550B 供电配置")
    for node, name, requested_min, requested_max in (
        ("ldo1", "vreg_l1b_1p8", 1800000, 1800000),
        ("ldo8", "vreg_l8b_1p8", 3296000, 3300000),
    ):
        rail = pmic / node
        minimum = int.from_bytes((rail / "regulator-min-microvolt").read_bytes(), "big")
        maximum = int.from_bytes((rail / "regulator-max-microvolt").read_bytes(), "big")
        if (rail / "regulator-name").read_bytes().rstrip(b"\0") != name.encode() or max(
                minimum, requested_min) > min(maximum, requested_max):
            raise RuntimeError(f"板上供电轨 {node} 的名称或约束与候选不一致")


class Trial:
    def __init__(self, firmware=None, diagnostics=False):
        self.firmware = FIRMWARE if firmware is None else firmware
        self.diagnostics = diagnostics
        self.loaded = []
        self.created_firmware = False
        self.gpio_device = None
        self.original_gpio_bound = None

    def find_gpio_keys(self, tree):
        expected = (tree / "gpio-keys").resolve()
        status = expected / "status"
        if status.is_file() and status.read_bytes().rstrip(b"\0") not in (b"ok", b"okay"):
            raise RuntimeError("设备树中的 /gpio-keys 处于禁用状态")
        devices = []
        for device in PLATFORM_DEVICES.iterdir():
            node = device / "of_node"
            if node.is_symlink() and node.resolve() == expected:
                devices.append(device)
        if len(devices) != 1:
            raise RuntimeError(f"设备树 /gpio-keys 对应的平台设备数量为 {len(devices)}，需要核对当前启动配置")
        self.gpio_device = devices[0]
        self.original_gpio_bound = self.gpio_bound()
        state = "已绑定" if self.original_gpio_bound else "未绑定"
        print(f"按键设备 {self.gpio_device.name}，原始驱动状态：{state}", flush=True)

    def gpio_bound(self):
        if self.gpio_device is None or not self.gpio_device.is_dir():
            raise RuntimeError("gpio-keys 平台设备已消失")
        driver = self.gpio_device / "driver"
        if not driver.is_symlink():
            return False
        actual = driver.resolve()
        if actual != GPIO_DRIVER.resolve():
            raise RuntimeError(f"按键设备当前由 {actual.name} 驱动管理")
        return True

    def report_probe_failure(self, started):
        logs = kernel_logs(started, PROBE_LOG_PATTERN, 20)
        if logs:
            print(f"本次硬件探测日志\n{logs}", flush=True)
        names = (self.gpio_device.name, "a9c000", "lenovo,lenovo_keyboard")
        try:
            deferred = DEFERRED_DEVICES.read_text().splitlines()
        except OSError:
            return
        for line in deferred:
            fields = line.split(maxsplit=1)
            if fields and any(name in fields[0] for name in names):
                print(f"内核暂缓探测的设备：{line.strip()}", flush=True)

    def check(self, tree):
        self.find_gpio_keys(tree)
        dts = make_dts(tree, move_hall=False)
        check_supplies(PMIC)
        for name in (*MODULES, *OLD_MODULES):
            if (Path("/sys/module") / name).exists():
                raise RuntimeError(f"请先卸载已有键盘候选模块：{name}")
        if self.firmware.exists() or self.firmware.is_symlink():
            raise RuntimeError("已有同名 DTBO")
        if self.prepared_dtb(dts) is None and not shutil.which("dtc"):
            raise RuntimeError("当前设备树需要现场生成 DTBO，请先安装 Ubuntu 的 device-tree-compiler。")
        for name in MODULES:
            module = ROOT / "driver" / f"{name}.ko"
            magic = subprocess.check_output(["modinfo", "-F", "vermagic", str(module)], text=True).split()
            if not magic or magic[0] != platform.release():
                raise RuntimeError(f"{name} 的内核版本与当前系统不一致")
        return dts

    def start(self, dts):
        if self.original_gpio_bound is None or self.gpio_bound() != self.original_gpio_bound:
            raise RuntimeError("gpio-keys 的绑定状态在开始前发生变化，请重试")
        blob = self.build_firmware(dts)
        self.firmware.parent.mkdir(parents=True, exist_ok=True)
        with self.firmware.open("xb") as output:
            self.created_firmware = True
            output.write(blob)
        self.load(MODULES[0])
        print("磁吸键盘 UART 设备树覆盖已加载。", flush=True)
        started = time.time()
        self.load(MODULES[1])
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if KEYBOARD_READY.is_file() and KEYBOARD_READY.read_text().strip() == "1":
                return
            time.sleep(0.2)
        self.report_probe_failure(started)
        raise RuntimeError("键盘驱动未确认串口唤醒完成")

    @staticmethod
    def prepared_dtb(dts):
        for name in PREPARED_SOURCES:
            prepared = ROOT / name
            binary = prepared.with_suffix(".dtbo")
            if prepared.is_file() and binary.is_file() and prepared.read_text() == dts:
                return binary
        return None

    def build_firmware(self, dts):
        prepared = self.prepared_dtb(dts)
        if prepared is not None:
            return prepared.read_bytes()
        with tempfile.TemporaryDirectory() as work:
            blob = Path(work) / "pogo-keyboard.dtbo"
            compile_dtbo(dts, blob)
            return blob.read_bytes()

    def load(self, module):
        started = time.time()
        command = ["insmod", ROOT / "driver" / f"{module}.ko"]
        if self.diagnostics and module == MODULES[1]:
            command.append("log_hotkeys=1")
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError:
            report_module_failure(module, started)
            raise
        self.loaded.append(module)

    def stop(self):
        try:
            if MODULES[1] in self.loaded:
                subprocess.run(["rmmod", MODULES[1]], check=True)
            if MODULES[0] in self.loaded:
                subprocess.run(["rmmod", MODULES[0]], check=True)
        finally:
            if self.created_firmware:
                self.firmware.unlink()

    def observe(self, seconds):
        deadline = time.monotonic() + seconds
        previous = None
        seen = set()
        while time.monotonic() < deadline:
            current = set()
            for name_path in INPUTS.glob("event*/device/name"):
                try:
                    name = name_path.read_text().strip()
                except OSError:
                    continue
                if name in EXPECTED_INPUTS:
                    current.add(name)
            if current != previous:
                print("当前识别的 Pogo 输入设备 " + ("、".join(sorted(current)) or "无"), flush=True)
                seen.update(current)
                previous = current
            time.sleep(0.5)
        return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--start", action="store_true")
    action.add_argument("--rollback", action="store_true")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    trial = Trial(diagnostics=args.start)
    if not (args.start or args.rollback):
        trial.check(TREE)
        print("设备树、当前内核、模块和 gpio-keys 基线检查通过。")
        return
    if os.geteuid() != 0 or args.seconds < 1:
        raise PermissionError("临时试验需要 root 和正的测试时长")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("测试被终止"))
    lock = os.open("/run/lock/tb710fu-pogo-trial.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        shortcuts = None
        try:
            shortcuts = GnomeShortcuts.active()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"无法访问 GNOME 会话：{error}", flush=True)
        if shortcuts is not None:
            shortcuts.restore()
        if args.rollback:
            if shortcuts is None:
                raise RuntimeError("请在原测试账号的 GNOME Wayland 桌面登录后恢复快捷键。")
            print("已清理此账号的临时 GNOME 快捷键。", flush=True)
            return
        dts = trial.check(TREE)
        started = time.time()
        try:
            if shortcuts is not None:
                try:
                    shortcuts.apply()
                    print("已配置本次测试的 GNOME 全屏和左侧平铺快捷键。", flush=True)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    shortcuts.restore()
                    print(f"无法配置桌面快捷键，本次仍可测试键码：{error}", flush=True)
            else:
                print("当前无可用的 GNOME Wayland 会话。本次仍可测试键码。", flush=True)
            trial.start(dts)
            print(f"UART 唤醒已发出。请在 {args.seconds} 秒内测试顶排、四格键、Delete、触控板和拔插。", flush=True)
            seen = trial.observe(args.seconds)
        finally:
            try:
                trial.stop()
            finally:
                if shortcuts is not None:
                    shortcuts.restore()
                hotkeys = kernel_logs(started, "Pogo hotkey ", 40)
                if hotkeys:
                    print(f"Pogo 功能键记录\n{hotkeys}", flush=True)
            if make_dts(TREE, move_hall=trial.original_gpio_bound) != dts or trial.gpio_bound() != trial.original_gpio_bound:
                raise RuntimeError("清理后尚未确认原设备树和 gpio-keys 已恢复，请重启。")
            print("临时模块已卸载，gpio-keys 已恢复测试前的绑定状态。", flush=True)
        missing = EXPECTED_INPUTS - seen
        if missing:
            raise RuntimeError("本次未识别到 " + "、".join(sorted(missing)) + "。请保留这次日志。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
