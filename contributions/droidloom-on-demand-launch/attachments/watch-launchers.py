#!/usr/bin/python3
import ctypes
import os
from pathlib import Path
import select
import subprocess
import sys

libc = ctypes.CDLL(None, use_errno=True)
fd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
if fd < 0:
    raise OSError(ctypes.get_errno(), "inotify_init1")
folder = Path.home() / ".local/share/applications"
folder.mkdir(parents=True, exist_ok=True)
if libc.inotify_add_watch(fd, os.fsencode(folder), 0x00000008 | 0x00000080) < 0:
    raise OSError(ctypes.get_errno(), "inotify_add_watch")
fixer = str(Path.home() / ".local/bin/droidloom-fix-launchers")
subprocess.run([fixer], check=True)
while True:
    select.select([fd], [], [])
    try:
        os.read(fd, 65536)
    except BlockingIOError:
        continue
    subprocess.run([fixer], check=True)
