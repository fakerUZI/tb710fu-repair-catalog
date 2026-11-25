#!/usr/bin/python3 -I
"""Stop the current desktop user's idle Droidloom runtime."""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time

TIMEOUT = 120
POLL = 5
RUNTIME = Path(os.environ["XDG_RUNTIME_DIR"])
LOCK = RUNTIME / "droidloom-on-demand.lock"


def inspect():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(15)
        sock.connect("/run/tb710fu-droidloom-inspect.sock")
        with sock.makefile("rb") as stream:
            state = json.loads(stream.readline(256))
    if type(state.get("init")) is not int or type(state.get("busy")) is not bool:
        raise ValueError("Android activity snapshot unavailable")
    return None if state["busy"] else state["init"]


def main():
    generation = None
    uncertain = False
    LOCK.touch(exist_ok=True)
    while subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                          "droidloom.service"]).returncode == 0:
        try:
            stamp = LOCK.stat().st_mtime_ns
            init = inspect()
            current = (init, stamp) if init else None
            if current != generation:
                generation = current
                since = time.monotonic()
            uncertain = False
            if generation and time.monotonic() - since >= TIMEOUT:
                # Only serialize the final check and stop with launches. Reading
                # Android stacks during ordinary samples must not delay a launch.
                with LOCK.open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if os.fstat(lock.fileno()).st_mtime_ns == stamp and inspect() == init:
                        print("No Android apps for 120 seconds; stopping Droidloom", flush=True)
                        subprocess.run(["/usr/bin/droidloomctl", "stop"], check=True, timeout=180)
                        return
                    generation = None
        except BlockingIOError:
            generation = None
        except (OSError, ValueError, subprocess.SubprocessError):
            generation = None
            if not uncertain:
                print("Could not confirm idle or complete stop; restarting idle observation", flush=True)
            uncertain = True
        time.sleep(POLL)


if __name__ == "__main__":
    main()
