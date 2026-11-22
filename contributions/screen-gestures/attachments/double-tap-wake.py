#!/usr/bin/python3
"""Apply the user's double-tap preference and wake their locked GNOME display."""
import os
from pathlib import Path
import signal
import subprocess

from gi.repository import Gio, GLib


SCHEMA = "org.tb710fu.screen-gestures"
KEY = "double-tap-to-wake"
CONTROL = Path("/sys/bus/mipi-dsi/devices/ae94000.dsi.0/dt2w_enabled")
TAPS = Path("/sys/module/nvt_36xxx/parameters/dt2w_taps")
DISPLAYS = Path("/sys/class/drm")
DISPLAY_CONFIG = "org.gnome.Mutter.DisplayConfig"
DISPLAY_OBJECT = "/org/gnome/Mutter/DisplayConfig"


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=5).stdout.strip()


def set_display_power(value):
    run("busctl", "--user", "set-property", DISPLAY_CONFIG, DISPLAY_OBJECT,
        DISPLAY_CONFIG, "PowerSaveMode", "i", str(value))


def wake_locked_display():
    panels = sorted(DISPLAYS.glob("card*-DSI-*/dpms"))
    if len(panels) != 1 or panels[0].read_text().strip() != "Off":
        return
    lid = subprocess.run(
        ("journalctl", "-b", "-u", "tb-hall-cover.service",
         "--grep=^cover (closed|opened)$", "--lines=1", "--output=cat", "--no-pager"),
        capture_output=True, text=True, timeout=3,
    )
    if lid.stderr or lid.returncode not in (0, 1) or lid.stdout.strip() == "cover closed":
        return

    locked = run("busctl", "--user", "call", "org.gnome.ScreenSaver",
                 "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "GetActive")
    if locked != "b true":
        return

    mode = run("busctl", "--user", "get-property", DISPLAY_CONFIG, DISPLAY_OBJECT,
               DISPLAY_CONFIG, "PowerSaveMode")
    if mode == "i 0":
        # GNOME can report 0 even when gsd-power has blanked the DSI output.
        set_display_power(3)
    set_display_power(0)
    run("/usr/local/bin/tb-backlight-power", "on")
    print("Double tap requested a locked display wake.", flush=True)


class WakeService:
    def __init__(self, settings):
        self.settings = settings
        self.fd = None
        self.watch = None
        self.counter = 0
        self.enabled = None

    def read_counter(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        return int(os.read(self.fd, 64))

    def on_tap(self, _channel, condition):
        try:
            if condition & GLib.IO_NVAL:
                raise ValueError("gesture counter disconnected")
            counter = self.read_counter()
        except (OSError, ValueError):
            self.enabled = None
            self.watch = None
            self.close_counter()
            return GLib.SOURCE_REMOVE
        try:
            changed, self.counter = counter != self.counter, counter
            if changed and self.settings.get_boolean(KEY) and os.access(CONTROL, os.W_OK):
                wake_locked_display()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"Double-tap wake: {exc}", flush=True)
        return GLib.SOURCE_CONTINUE

    def close_counter(self):
        if self.watch is not None:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def reconcile(self, *_args):
        desired = self.settings.get_boolean(KEY)
        if not os.access(CONTROL, os.W_OK):
            self.enabled = None
            self.close_counter()
            return GLib.SOURCE_CONTINUE
        try:
            if desired and self.fd is None:
                self.fd = os.open(TAPS, os.O_RDONLY | os.O_CLOEXEC)
                self.counter = self.read_counter()
                self.watch = GLib.io_add_watch(self.fd, GLib.PRIORITY_DEFAULT,
                                                GLib.IO_PRI | GLib.IO_ERR | GLib.IO_NVAL,
                                                self.on_tap)
            applied = CONTROL.read_text().strip() == "1"
            if applied != desired:
                CONTROL.write_text(f"{int(desired)}\n")
            if not desired:
                self.close_counter()
            if self.enabled is None or self.enabled != desired:
                print(f"Double-tap wake {'enabled' if desired else 'disabled'}.", flush=True)
            self.enabled = desired
        except (OSError, ValueError) as exc:
            if self.enabled is not None:
                print(f"Double-tap setting: {exc}", flush=True)
            self.enabled = None
            self.close_counter()
        return GLib.SOURCE_CONTINUE

    def stop(self):
        self.close_counter()
        if self.enabled and os.access(CONTROL, os.W_OK):
            try:
                CONTROL.write_text("0\n")
            except OSError:
                pass


def main():
    settings = Gio.Settings.new(SCHEMA)
    service = WakeService(settings)
    loop = GLib.MainLoop()
    settings.connect(f"changed::{KEY}", service.reconcile)
    GLib.timeout_add_seconds(1, service.reconcile)
    for number in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, loop.quit)
    service.reconcile()
    try:
        loop.run()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
