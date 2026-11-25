#!/usr/bin/python3 -I
"""Apply TB710FU frequency limits when Ubuntu's desktop power profile changes."""
from pathlib import Path
import json
import sys

from gi.repository import Gio, GLib

CPU = Path("/sys/devices/system/cpu/cpufreq")
GPU = Path("/sys/class/devfreq/3d00000.gpu")
SLICE = Path("/sys/kernel/debug/sched/base_slice_ns")
BACKUP = Path("/run/tb710fu-profile-tuning/original.json")
IFACE = "org.freedesktop.UPower.PowerProfiles"
# power-tuning 2026.11.24-1, in policy0,3,5,7 order (kHz), GPU in Hz.
LIMITS = {
    "balanced": ((1804800, 2515200, 2515200, 2438400), 680000000, 1000),
    "power-saver": ((1574400, 1824000, 1824000, 1824000), 500000000, 2000),
}


def limits(profile):
    maxima, gpu_max, rate = LIMITS[profile]
    result = {}
    for index, cap in zip((0, 3, 5, 7), maxima):
        policy = CPU / f"policy{index}"
        freqs = [int(v) for v in (policy / "scaling_available_frequencies").read_text().split()]
        result[policy / "scaling_max_freq"] = max(v for v in freqs if v <= cap)
        node = policy / "schedutil/rate_limit_us"
        if node.exists():
            result[node] = rate
    available = [int(v) for v in (GPU / "available_frequencies").read_text().split()]
    result[GPU / "max_freq"] = max(v for v in available if v <= gpu_max)
    if (GPU / "polling_interval").exists():
        result[GPU / "polling_interval"] = 20
    if SLICE.exists():
        result[SLICE] = 500000
    return result


def write_values(values):
    changed = {}
    try:
        for path, target in values.items():
            previous = int(path.read_text())
            if previous != int(target):
                path.write_text(str(target))
                changed[path] = previous
    except (OSError, ValueError):
        for path, previous in reversed(changed.items()):
            try:
                path.write_text(str(previous))
            except OSError as error:
                print(f"Could not restore {path}: {error}", file=sys.stderr, flush=True)
        raise


def restore():
    if BACKUP.exists():
        values = json.loads(BACKUP.read_text())
        write_values({Path(p): v for p, v in values.items()})


def main():
    if sys.argv[1:] == ["restore"]:
        restore()
        return
    if b"lenovo,tb710fu" not in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"):
        raise SystemExit("Unsupported tablet")
    proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE,
        None, IFACE, "/org/freedesktop/UPower/PowerProfiles", IFACE, None)
    def apply(*unused):
        active = proxy.get_cached_property("ActiveProfile")
        if not active or active.unpack() not in LIMITS:
            restore()
            return
        profile = active.unpack()
        values = limits(profile)
        if not BACKUP.exists():
            original = {str(p): int(p.read_text()) for p in values}
            temporary = BACKUP.with_suffix(".tmp")
            temporary.write_text(json.dumps(original))
            temporary.replace(BACKUP)
        write_values(values)
        print(f"Applied {profile} CPU/GPU scheduler limits", flush=True)

    proxy.connect("g-properties-changed", apply)
    proxy.connect("notify::g-name-owner", apply)
    apply()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
