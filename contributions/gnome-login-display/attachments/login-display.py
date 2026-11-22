#!/usr/bin/python3
"""Reinitialize the built-in 144 Hz panel once after GNOME login."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import time


DISPLAY = "org.gnome.Mutter.DisplayConfig"
DISPLAY_PATH = "/org/gnome/Mutter/DisplayConfig"
SAVER = "org.gnome.ScreenSaver"
SAVER_PATH = "/org/gnome/ScreenSaver"
PROPERTIES = "org.freedesktop.DBus.Properties"
DRM = Path("/sys/class/drm")
ON, OFF = 0, 3


def active_session():
    session = subprocess.run(
        ("/usr/bin/loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value"),
        check=True, capture_output=True, text=True, timeout=2,
    ).stdout.strip()
    if not session:
        return None
    properties = subprocess.run(
        ("/usr/bin/loginctl", "show-session", session, "-p", "User", "-p", "Type",
         "-p", "Class", "-p", "Active", "-p", "Remote", "-p", "LockedHint"),
        check=True, capture_output=True, text=True, timeout=2,
    ).stdout
    values = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    if (values.get("User") == str(os.getuid()) and values.get("Type") == "wayland"
            and values.get("Class") == "user" and values.get("Active") == "yes"
            and values.get("Remote") == "no" and values.get("LockedHint") == "no"):
        return session
    return None


def panel_144(state):
    serial, monitors, logical, _props = state
    if len(monitors) != 1 or len(logical) != 1:
        return None
    spec, modes, props = monitors[0]
    if (not spec[0].startswith("DSI-") or not props.get("is-builtin")
            or logical[0][5] != [spec]):
        return None
    current = [mode for mode in modes if mode[6].get("is-current")]
    if len(current) != 1 or tuple(current[0][1:3]) != (3200, 2000):
        return None
    if abs(current[0][3] - 144) >= 0.1:
        return None
    return serial, spec[0]


def panel_status(connector):
    matches = list(DRM.glob(f"card[0-9]*-{connector}/dpms"))
    return matches[0] if len(matches) == 1 else None


class SessionBus:
    def __init__(self, Gio, GLib):
        self.gio = Gio
        self.glib = GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(self, destination, path, interface, method, signature=None, args=()):
        payload = self.glib.Variant(signature, args) if signature else None
        return self.bus.call_sync(destination, path, interface, method, payload, None,
                                  self.gio.DBusCallFlags.NO_AUTO_START, 1500, None).unpack()

    def locked(self):
        return self.call(SAVER, SAVER_PATH, SAVER, "GetActive")[0]

    def state(self):
        return self.call(DISPLAY, DISPLAY_PATH, DISPLAY, "GetCurrentState")

    def mode(self):
        return self.call(DISPLAY, DISPLAY_PATH, PROPERTIES, "Get", "(ss)", (DISPLAY, "PowerSaveMode"))[0]

    def set_mode(self, mode):
        self.call(DISPLAY, DISPLAY_PATH, PROPERTIES, "Set", "(ssv)",
                  (DISPLAY, "PowerSaveMode", self.glib.Variant("i", mode)))


def wait_dpms(path, expected, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.read_text().strip() == expected:
            return True
        time.sleep(0.08)
    return False


def cycle(bus, dpms, transient_errors):
    try:
        bus.set_mode(OFF)
        if not wait_dpms(dpms, "Off", 1.4):
            raise RuntimeError("内屏未进入 DPMS Off")
        time.sleep(0.3)
    finally:
        for attempt in range(3):
            try:
                bus.set_mode(ON)
                if wait_dpms(dpms, "On", 1.25):
                    break
            except transient_errors:
                if attempt == 2:
                    raise
            time.sleep(0.15)
        else:
            raise RuntimeError("内屏未报告 DPMS On")


def recover(bus, last_session, transient_errors):
    deadline = time.monotonic() + 42
    previous = None
    stable_since = 0.0
    while time.monotonic() < deadline:
        try:
            session = active_session()
            if session and last_session.exists() and last_session.read_text().strip() == session:
                print("本次登录已经尝试过屏幕恢复。")
                return
            if session and not bus.locked():
                state = bus.state()
                candidate = panel_144(state)
                key = (session, state[0], candidate)
                now = time.monotonic()
                if key != previous:
                    previous, stable_since = key, now
                elif now - stable_since >= (1.5 if candidate else 5):
                    if candidate is None:
                        print("当前未使用单独的 144 Hz 内屏，跳过显示恢复。")
                        return
                    dpms = panel_status(candidate[1])
                    if (dpms and bus.mode() == ON and dpms.read_text().strip() == "On"
                            and active_session() == session and not bus.locked()):
                        break
            else:
                previous = None
        except transient_errors:
            previous = None
        time.sleep(0.45)
    else:
        print("没有等到已解锁且可用的 GNOME 内屏，跳过显示恢复。")
        return
    last_session.write_text(session + "\n")
    cycle(bus, dpms, transient_errors)
    print("144 Hz 内屏完成登录后的 DPMS 恢复。")


def interrupted(_number, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    from gi.repository import Gio, GLib
    bus = SessionBus(Gio, GLib)
    if args.check:
        eligible = panel_144(bus.state())
        print("当前桌面符合 144 Hz 单内屏条件。" if eligible else
              "当前桌面无需显示恢复，下次登录会重新检测。")
        return
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    with (runtime / "tb710fu-login-display.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recover(bus, runtime / "tb710fu-login-display.session",
                (GLib.Error, OSError, subprocess.SubprocessError))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        raise SystemExit(f"登录显示恢复：{error}") from None
