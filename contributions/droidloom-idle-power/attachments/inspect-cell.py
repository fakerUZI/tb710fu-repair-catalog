#!/usr/bin/python3 -I
"""Return one read-only activity snapshot to the configured desktop user."""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess

HOME_ACTIVITY = "com.android.droidloom.home/"
HISTORY = re.compile(r"^[ \t]*(?:\*\s+)?Hist\s+#\d+:\s+ActivityRecord\{[^\n]*?\bu\d+\s+([^\s}]+)", re.M)


def has_task(activities):
    # Active task history includes stopped/minimized activities on other displays.
    # Recents can retain dead tasks indefinitely, so only read the live stacks.
    if not (activities.startswith("ACTIVITY MANAGER ACTIVITIES") and
            re.search(r"^\s*Display #0\b", activities, re.M)):
        raise ValueError("unrecognized Android activity dump")
    records = HISTORY.findall(activities)
    if not records or len(records) != len(re.findall(r"^[ \t]*(?:\*\s+)?Hist\b", activities, re.M)):
        raise ValueError("incomplete Android activity history")
    return any(not component.startswith(HOME_ACTIVITY) for component in records)


def has_foreground_service(services):
    if not services.startswith("ACTIVITY MANAGER SERVICES"):
        raise ValueError("unrecognized Android service dump")
    return bool(re.search(r"\bisForeground=true\b", services))


def snapshot(uid):
    if json.loads(Path("/etc/droidloom/cell.json").read_text())["host_uid"] != uid:
        raise ValueError("wrong cell owner")
    # Resolve namespace PID 1 within the supervisor's own named network namespace.
    pids = subprocess.check_output(["/usr/sbin/ip", "netns", "pids", f"droidloom-u{uid}"],
                                   timeout=3, text=True).split()
    for pid in pids:
        if not pid.isdecimal():
            raise ValueError("unexpected process id")
        proc = Path("/proc") / pid
        try:
            status = (proc / "status").read_text()
            if not re.search(r"^NSpid:\s+" + pid + r"\s+1$", status, re.M):
                continue
            if (proc / "cmdline").read_bytes().split(b"\0")[:2] != [b"/init", b"second_stage"]:
                continue
            prefix = ["/usr/bin/nsenter", "--target", pid, "--mount", "--pid", "--net",
                      "--ipc", "--uts", "--root", "--wd", "--setgid=2000",
                      "--setuid=2000", "--", "/system/bin/dumpsys", "-t", "3", "activity"]

            def dump(name):
                args = ["-a", name] if name == "services" else [name]
                output = subprocess.check_output(
                    prefix + args, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, text=True, env={"PATH": "/system/bin", "ANDROID_ROOT": "/system",
                                               "ANDROID_DATA": "/data"})
                if "TIMEOUT" in output or "Permission Denial" in output:
                    raise ValueError("incomplete dump")
                return output

            busy = has_task(dump("activities")) or has_foreground_service(dump("services"))
            return {"init": int(pid), "busy": busy}
        except FileNotFoundError:
            continue
    raise ValueError("cell unavailable")


if __name__ == "__main__":
    with socket.socket(fileno=0) as peer:
        _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        try:
            os.setgroups([])
            result = snapshot(uid)
        except (ValueError, KeyError, OSError, subprocess.SubprocessError):
            result = {}
        peer.sendall(json.dumps(result).encode() + b"\n")
