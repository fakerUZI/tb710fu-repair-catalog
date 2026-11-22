#!/usr/bin/python3
"""Grant only the active local session access to the tablet's power controls."""
import glob
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, '/usr/local/lib')
from tb710fu_session import active_user

BACKLIGHT = Path('/sys/class/backlight/sy7758-backlight')
GESTURES = Path('/sys/bus/mipi-dsi/devices/ae94000.dsi.0/dt2w_enabled')


def key_devices():
    return sorted(glob.glob('/dev/input/by-path/*:pwrkey-event'))


def set_key_acl(path, uid, grant):
    action = f'u:{uid}:rw' if grant else f'u:{uid}'
    subprocess.run(['setfacl', '-m' if grant else '-x', action, path],
                   timeout=3, check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def main():
    previous = None
    known_keys = ()
    while True:
        user = active_user()
        uid = user[0] if user else None
        keys = tuple((p, os.stat(p).st_ino) for p in key_devices() if os.path.exists(p))
        if uid != previous or keys != known_keys:
            if previous is not None:
                for path, _ in known_keys:
                    if os.path.exists(path):
                        set_key_acl(path, previous, False)
            if uid is not None:
                for path, _ in keys:
                    set_key_acl(path, uid, True)
            previous, known_keys = uid, keys
        # sysfs does not support ACLs. Assign the active seat user only the
        # tablet's display controls and optional rear-camera focus control.
        attributes = [BACKLIGHT / 'brightness', BACKLIGHT / 'bl_power',
                      Path('/sys/module/tb_focus_manual/parameters/position')]
        for p in attributes:
            try:
                if not p.exists():
                    continue
                owner = uid if uid is not None else 0
                if p.stat().st_uid != owner:
                    os.chown(p, owner, -1)
            except OSError as exc:
                print(f'backlight access: {exc}', flush=True)
        try:
            owner = uid if uid is not None else 0
            if GESTURES.exists() and GESTURES.stat().st_uid != owner:
                # Revoke the old owner's access before clearing their preference.
                os.chown(GESTURES, 0, -1)
                GESTURES.write_text('0\n')
                os.chown(GESTURES, owner, -1)
        except OSError as exc:
            print(f'double tap access: {exc}', flush=True)
        time.sleep(2)


if __name__ == '__main__':
    main()
