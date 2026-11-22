#!/usr/bin/python3
"""Recover the built-in panel after GNOME login and 60 Hz unlock."""
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
BACKLIGHT = Path("/sys/class/backlight/sy7758-backlight")
LOGIN_RATES = (60, 144)
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


def panel_mode(state, rates):
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
    for rate in rates:
        if abs(current[0][3] - rate) < 0.1:
            return serial, spec[0], rate
    return None


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


def restore_backlight():
    power = BACKLIGHT / "bl_power"
    brightness = BACKLIGHT / "brightness"
    if not power.is_file() or not brightness.is_file():
        return
    if power.read_text().strip() == "0" and int(brightness.read_text()) > 0:
        return
    subprocess.run(("/usr/local/bin/tb-backlight-power", "on"), timeout=3,
                   check=True, stdout=subprocess.DEVNULL)
    if power.read_text().strip() != "0" and int(brightness.read_text()) > 0:
        power.write_text("0\n")


def restore_panel(bus, dpms, rate, event, transient_errors):
    cycle(bus, dpms, transient_errors)
    if active_session() and not bus.locked():
        restore_backlight()
    print(f"TB710FU display recovered: {event}, {rate} Hz, DPMS On.", flush=True)


def recover(bus, last_session, transient_errors):
    deadline = time.monotonic() + 42
    previous = None
    stable_since = 0.0
    while time.monotonic() < deadline:
        try:
            session = active_session()
            if session and last_session.exists() and last_session.read_text().strip() == session:
                return False
            if session and not bus.locked():
                state = bus.state()
                candidate = panel_mode(state, LOGIN_RATES)
                key = (session, state[0], candidate)
                now = time.monotonic()
                if key != previous:
                    previous, stable_since = key, now
                elif now - stable_since >= (1.5 if candidate else 5):
                    if candidate is None:
                        print("当前未使用单独的 60 / 144 Hz 内屏，跳过登录恢复。", flush=True)
                        return False
                    dpms = panel_status(candidate[1])
                    if dpms and active_session() == session and not bus.locked():
                        break
            else:
                previous = None
        except transient_errors:
            previous = None
        time.sleep(0.45)
    else:
        print("没有等到已解锁且可用的 GNOME 内屏，跳过登录恢复。", flush=True)
        return False
    last_session.write_text(session + "\n")
    restore_panel(bus, dpms, candidate[2], "login", transient_errors)
    return True


class UnlockRecovery:
    def __init__(self, bus, GLib, transient_errors):
        self.bus = bus
        self.glib = GLib
        self.errors = transient_errors
        self.was_locked = False
        self.pending = None
        self.deadline = 0
        self.last_recovery = float("-inf")

    def listen(self):
        self.bus.bus.signal_subscribe(
            SAVER, SAVER, "ActiveChanged", SAVER_PATH, None,
            self.bus.gio.DBusSignalFlags.NONE, self.changed,
        )
        try:
            self.was_locked = self.bus.locked()
        except self.errors:
            pass

    def changed(self, _bus, _sender, _path, _interface, _signal, arguments):
        locked = arguments.unpack()[0]
        if locked:
            self.was_locked = True
            if self.pending is not None:
                self.glib.source_remove(self.pending)
                self.pending = None
        elif self.was_locked:
            self.was_locked = False
            self.deadline = time.monotonic() + 4
            self.pending = self.glib.timeout_add(350, self.after_unlock)

    def after_unlock(self):
        if time.monotonic() < self.deadline and time.monotonic() - self.last_recovery >= 3:
            try:
                if self.bus.locked():
                    self.pending = None
                    return self.glib.SOURCE_REMOVE
                if not active_session():
                    return self.glib.SOURCE_CONTINUE
                candidate = panel_mode(self.bus.state(), (60,))
                dpms = panel_status(candidate[1]) if candidate else None
            except self.errors:
                return self.glib.SOURCE_CONTINUE
            if dpms:
                self.last_recovery = time.monotonic()
                try:
                    restore_panel(self.bus, dpms, candidate[2], "unlock", self.errors)
                except (*self.errors, RuntimeError) as error:
                    print(f"TB710FU unlock display recovery failed: {error}", flush=True)
        self.pending = None
        return self.glib.SOURCE_REMOVE


def interrupted(_number, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    from gi.repository import Gio, GLib
    bus = SessionBus(Gio, GLib)
    if args.check:
        eligible = panel_mode(bus.state(), LOGIN_RATES)
        print(f"当前桌面符合 {eligible[2]} Hz 单内屏条件。" if eligible else
              "当前桌面无需显示恢复，下次登录会重新检测。")
        return
    signals = (signal.SIGINT, signal.SIGTERM)
    for signum in signals:
        signal.signal(signum, interrupted)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    with (runtime / "tb710fu-login-display.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        errors = (GLib.Error, OSError, ValueError, subprocess.SubprocessError)
        unlock = UnlockRecovery(bus, GLib, errors)
        unlock.listen()
        try:
            if recover(bus, runtime / "tb710fu-login-display.session", errors):
                unlock.last_recovery = time.monotonic()
        except (*errors, RuntimeError) as error:
            print(f"TB710FU login display recovery failed: {error}", flush=True)
        loop = GLib.MainLoop()

        def stop():
            loop.quit()
            return GLib.SOURCE_REMOVE

        for signum in signals:
            signal.signal(signum, signal.SIG_DFL)
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, stop)
        loop.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        raise SystemExit(f"登录显示恢复：{error}") from None
