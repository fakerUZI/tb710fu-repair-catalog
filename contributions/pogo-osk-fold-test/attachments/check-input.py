#!/usr/bin/python3
"""Run on TB710FU from the Ubuntu graphical user's terminal: sudo python3 check-input.py."""
import errno
import os
from pathlib import Path
import selectors
import struct
import subprocess
import sys
import time

INPUT = Path("/sys/class/input")
EVENT = struct.Struct("@llHHi")  # Linux input_event on the tablet's arm64 userspace


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "未知"


def command(args):
    env = dict(os.environ, LC_ALL="C")
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else result.stderr.strip()


def show_display():
    uid = os.getenv("SUDO_UID")
    args = ["gdctl", "show"]
    if os.getuid() == 0 and uid and uid != "0":
        import pwd
        user = pwd.getpwuid(int(uid)).pw_name
        runtime = f"/run/user/{int(uid)}"
        args = ["runuser", "-u", user, "--", "env", f"XDG_RUNTIME_DIR={runtime}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus", *args]
    lines = command(args).splitlines()
    for index, line in enumerate(lines[:-1]):
        if "Current mode" in line:
            print("当前显示模式", lines[index + 1].strip())
    print("系统合盖状态", command(["busctl", "--system", "get-property",
          "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", "LidIsClosed"]))


def show_display_history():
    print("面板模块版本标记", read("/sys/module/panel_nt36532_wqhd_dual_dsi_dsc/srcversion"))
    for status in sorted(Path("/sys/class/drm").glob("card*-*/status")):
        if read(status) == "connected":
            print("已连接显示接口", status.parent.name)
    pattern = "MDP FIFO underflow|Recover for fw reset|flip_done timed out|commit wait timed out"
    result = subprocess.run(["journalctl", "-b", "-k", "--since=-10min", "--no-pager",
                             "--case-sensitive=no", "--grep", pattern, "-o", "cat"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1) or result.stderr.strip():
        print("未能读取完整的近期内核日志")
        return
    counts = [result.stdout.lower().count(word.lower()) for word in pattern.split("|")]
    print(f"最近 10 分钟内核日志：显示 FIFO 欠载 {counts[0]}，触控固件复位 {counts[1]}，"
          f"显示提交超时 {sum(counts[2:])}")


def sample():
    show_display_history()
    show_display()
    print("内核", os.uname().release, "触控模块", read("/sys/module/nvt_36xxx/version"),
          "请求的笔扫描状态", read("/proc/support_pen"))
    presses = pressure_events = peak = 0
    files = []
    try:
        with selectors.DefaultSelector() as selector:
            for entry in INPUT.glob("event*"):
                dev = entry / "device"
                name = read(dev / "name")
                if name == "NVTCapacitivePen":
                    kind = "pen"
                elif (name == "Lenovo Keyboard Pack For Yoga Tab Keyboard"
                      and read(dev / "phys") == "input/lenovo_kb"):
                    kind = "pogo"
                else:
                    continue
                f = open(Path("/dev/input") / entry.name, "rb", buffering=0)
                files.append(f)
                selector.register(f, selectors.EVENT_READ, kind)
            if not any(key.data == "pen" for key in selector.get_map().values()):
                raise RuntimeError("当前内核未注册 NVTCapacitivePen")
            print("请在 12 秒内用笔轻重划几笔。若测试磁吸键盘，可同时背折、展开各一次。", flush=True)
            deadline = time.monotonic() + 12
            while (remaining := deadline - time.monotonic()) > 0:
                for key, _ in selector.select(remaining):
                    try:
                        data = key.fileobj.read(EVENT.size * 64)
                    except OSError as error:
                        if error.errno != errno.ENODEV:
                            raise
                        if key.data == "pen":
                            raise RuntimeError("采样时笔设备断开，请重新运行") from error
                        data = b""
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    for _, _, typ, code, value in EVENT.iter_unpack(data):
                        if key.data == "pen":
                            if (typ, code, value) == (1, 330, 1):  # BTN_TOUCH
                                presses += 1
                            if typ == 3 and code == 24 and value > 0:  # ABS_PRESSURE
                                pressure_events += 1
                                peak = max(peak, value)
                        elif typ == 1 and value == 1 and code in (0x28e, 0x28f):
                            print("磁吸键盘驱动上报", "启用" if code == 0x28e else "禁用", flush=True)
    finally:
        for f in files:
            f.close()
    print(f"落笔次数 {presses}，正压力事件 {pressure_events}，峰值 {peak}")
    show_display()


if __name__ == "__main__":
    try:
        sample()
    except PermissionError:
        sys.exit("读取输入设备需要权限，请从 Ubuntu 桌面终端运行 sudo python3 check-input.py。")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
