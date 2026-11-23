#!/usr/bin/env python3
"""Supervised TB710FU trial with restoration of the original key binding."""

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

from build_overlay import make_dts


ROOT = Path(__file__).resolve().parent
TREE = Path("/sys/firmware/devicetree/base")
PLATFORM_DEVICES = Path("/sys/bus/platform/devices")
GPIO_DRIVER = Path("/sys/bus/platform/drivers/gpio-keys")
KEYBOARD_READY = Path("/sys/bus/platform/devices/lenovo,lenovo_keyboard/transport_ready")
INPUTS = Path("/sys/class/input")
FIRMWARE = Path("/usr/lib/firmware/tb710fu/pogo-keyboard.dtbo")
PMIC = TREE / "soc@0/rsc@17a00000/regulators-0"
MODULES = ("tb_pogo_overlay", "lenovo_keyboard")
OLD_MODULES = ("uartov", "rpmhmain", "voltset")
EXPECTED_INPUTS = {
    "Lenovo Keyboard Pack For Yoga Tab Keyboard",
    "Lenovo Keyboard Pack For Yoga Tab Touchpad",
}


class Trial:
    def __init__(self):
        self.loaded = []
        self.created_firmware = False
        self.touched_keys = False
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
        if not (GPIO_DRIVER / "bind").is_file() or not (GPIO_DRIVER / "unbind").is_file():
            raise RuntimeError(f"找到 {self.gpio_device.name}，但系统未注册可重绑的 gpio-keys 驱动")
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

    def set_gpio_bound(self, wanted):
        if self.gpio_bound() == wanted:
            return
        binding = GPIO_DRIVER / ("bind" if wanted else "unbind")
        try:
            binding.write_text(self.gpio_device.name)
        except OSError as error:
            raise RuntimeError(f"{binding.name} {self.gpio_device.name} 失败：{error.strerror}") from error
        if self.gpio_bound() != wanted:
            raise RuntimeError("gpio-keys 的绑定状态与请求不一致")

    def check(self, tree):
        dts = make_dts(tree)
        if (PMIC / "qcom,pmic-id").read_bytes() != b"b\0":
            raise RuntimeError("找不到已核对的 PM8550B 供电配置")
        for node, name, target in (
            ("ldo1", "vreg_l1b_1p8", 1800000),
            ("ldo8", "vreg_l8b_1p8", 3300000),
        ):
            rail = PMIC / node
            minimum = int.from_bytes((rail / "regulator-min-microvolt").read_bytes(), "big")
            maximum = int.from_bytes((rail / "regulator-max-microvolt").read_bytes(), "big")
            if (rail / "regulator-name").read_bytes().rstrip(b"\0") != name.encode() or not minimum <= target <= maximum:
                raise RuntimeError(f"板上供电轨 {node} 的名称或约束与候选不一致")
        for name in (*MODULES, *OLD_MODULES):
            if (Path("/sys/module") / name).exists():
                raise RuntimeError(f"请先卸载已有键盘候选模块：{name}")
        if FIRMWARE.exists() or FIRMWARE.is_symlink():
            raise RuntimeError("已有同名 DTBO")
        if self.prepared_dtb(dts) is None and not shutil.which("dtc"):
            raise RuntimeError("当前设备树需要现场生成 DTBO，请先安装 Ubuntu 的 device-tree-compiler。")
        for name in MODULES:
            module = ROOT / "driver" / f"{name}.ko"
            magic = subprocess.check_output(["modinfo", "-F", "vermagic", str(module)], text=True).split()
            if not magic or magic[0] != platform.release():
                raise RuntimeError(f"{name} 的内核版本与当前系统不一致")
        self.find_gpio_keys(tree)
        return dts

    def start(self, dts):
        if self.original_gpio_bound is None or self.gpio_bound() != self.original_gpio_bound:
            raise RuntimeError("gpio-keys 的绑定状态在开始前发生变化，请重试")
        blob = self.build_firmware(dts)
        FIRMWARE.parent.mkdir(parents=True, exist_ok=True)
        with FIRMWARE.open("xb") as output:
            self.created_firmware = True
            output.write(blob)
        self.touched_keys = True
        self.set_gpio_bound(False)
        self.load(MODULES[0])
        self.set_gpio_bound(True)
        self.load(MODULES[1])
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if KEYBOARD_READY.is_file() and KEYBOARD_READY.read_text().strip() == "1":
                return
            time.sleep(0.2)
        raise RuntimeError("键盘驱动未确认串口唤醒完成")

    @staticmethod
    def prepared_dtb(dts):
        prepared = ROOT / "pogo-keyboard.dts"
        binary = prepared.with_suffix(".dtbo")
        if prepared.is_file() and binary.is_file() and prepared.read_text() == dts:
            return binary
        return None

    def build_firmware(self, dts):
        prepared = self.prepared_dtb(dts)
        if prepared is not None:
            return prepared.read_bytes()
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "pogo.dts"
            blob = source.with_suffix(".dtbo")
            source.write_text(dts)
            subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", blob, source], check=True)
            return blob.read_bytes()

    def load(self, module):
        subprocess.run(["insmod", ROOT / "driver" / f"{module}.ko"], check=True)
        self.loaded.append(module)

    def stop(self):
        try:
            if MODULES[1] in self.loaded:
                subprocess.run(["rmmod", MODULES[1]], check=True)
            if MODULES[0] in self.loaded:
                self.set_gpio_bound(False)
                subprocess.run(["rmmod", MODULES[0]], check=True)
        finally:
            try:
                if self.touched_keys:
                    self.set_gpio_bound(self.original_gpio_bound)
            finally:
                if self.created_firmware:
                    FIRMWARE.unlink()

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
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    trial = Trial()
    if not args.start:
        trial.check(TREE)
        print("设备树、当前内核、模块和 gpio-keys 基线检查通过。")
        return
    if os.geteuid() != 0 or args.seconds < 1:
        raise PermissionError("临时试验需要 root 和正的测试时长")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("测试被终止"))
    lock = os.open("/run/lock/tb710fu-pogo-trial.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dts = trial.check(TREE)
        try:
            trial.start(dts)
            print(f"UART 唤醒已发出。请在 {args.seconds} 秒内测试按键、触控板和拔插。", flush=True)
            seen = trial.observe(args.seconds)
        finally:
            trial.stop()
            if make_dts(TREE) != dts or trial.gpio_bound() != trial.original_gpio_bound:
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
