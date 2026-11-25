"""Publish the two TB710FU posture pulses for the desktop. Input is Linux evdev."""
import errno
import os
from pathlib import Path
import re
import struct
import sys


EVENT = struct.Struct("@llHHi")
POSTURES = {0x28e: "enabled", 0x28f: "disabled"}


def publish_events(source, directory, input_id):
    temporary = directory / "state.new"
    while data := os.read(source, EVENT.size * 64):
        for _, _, kind, code, value in EVENT.iter_unpack(data):
            if kind == 1 and value == 1 and code in POSTURES:
                temporary.write_text(f"{input_id} {POSTURES[code]}\n")
                temporary.replace(directory / "state")


def main(node):
    if not re.fullmatch(r"event[0-9]+", node):
        raise ValueError("Expected an evdev event node")
    dev = (Path("/sys/class/input") / node / "device").resolve(strict=True)
    identity = [ (dev / field).read_text().strip()
                 for field in ("phys", "id/bustype", "id/vendor", "id/product") ]
    if identity != ["input/lenovo_kb", "0019", "17ef", "61ba"]:
        raise ValueError("Unexpected input device")
    with open(Path("/dev/input") / node, "rb", buffering=0) as stream:
        if (Path("/sys/class/input") / node / "device").resolve(strict=True) != dev:
            return
        publish_events(stream.fileno(), Path(os.environ["RUNTIME_DIRECTORY"]), dev.name)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except OSError as error:
        if error.errno not in (errno.ENODEV, errno.ENOENT):
            raise
