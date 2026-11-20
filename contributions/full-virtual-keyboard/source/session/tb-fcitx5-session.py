#!/usr/bin/env python3
"""Wait for GNOME's imported display environment before starting Fcitx.

--check validates the present session without restarting the input method.
No input contents or authentication cookie contents are read or logged.
"""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time


def session_environment():
    result = subprocess.run(['systemctl', '--user', 'show-environment'],
                            capture_output=True, text=True, timeout=5, check=True)
    environment = dict(os.environ)
    # Clear stale display values before importing the current graphical session.
    keys = ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR')
    for key in keys:
        environment.pop(key, None)
    for line in result.stdout.splitlines():
        key, separator, value = line.partition('=')
        if separator and key in keys:
            environment[key] = value
    return environment


def ready(environment):
    if not environment.get('DISPLAY'):
        return False
    authority = environment.get('XAUTHORITY')
    if authority and not Path(authority).is_file():
        return False
    runtime = environment.get('XDG_RUNTIME_DIR')
    wayland = environment.get('WAYLAND_DISPLAY')
    if wayland:
        if not runtime:
            return False
        socket = Path(wayland) if wayland.startswith('/') else Path(runtime) / wayland
        if not socket.is_socket():
            return False
    # The path alone is insufficient: verify that this identity can open X11.
    old = {key: os.environ.get(key) for key in ('DISPLAY', 'XAUTHORITY')}
    try:
        for key in old:
            if key in environment:
                os.environ[key] = environment[key]
            else:
                os.environ.pop(key, None)
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        display = x11.XOpenDisplay(None)
        if not display:
            return False
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay(display)
        return True
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    deadline = time.monotonic() + (0 if args.check else 90)
    while True:
        try:
            environment = session_environment()
            if ready(environment):
                if args.check:
                    print('PASS: current session environment and X11 connection ready')
                    return 0
                os.execve('/usr/bin/fcitx5', ['fcitx5', '-D', '-r'], environment)
        except (OSError, subprocess.SubprocessError):
            pass
        if time.monotonic() >= deadline:
            print('Graphical display environment is not ready; Fcitx service may retry.', file=sys.stderr)
            return 1
        time.sleep(1)


if __name__ == '__main__':
    sys.exit(main())
