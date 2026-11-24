#!/usr/bin/python3
"""Confirm a clean BOOTFIX installation after its first real Ubuntu boot."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import zlib


BOOT_SHA = "12ba30a883b792d8f89dddd21f759558d26c5c833b2d5cc61b0d35e8233da977"
BOOT_BYTES = 96 << 20
KERNEL = "7.2.6-tb710fu-20261118"
ACTIVE = 1 << 50
RETRIES = 7 << 51
SUCCESS = 1 << 54
UNBOOTABLE = 1 << 55
STATE = Path("/var/lib/tb710fu-boot-slot")


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def require(condition, message):
    if not condition:
        raise ValueError(message)


def partition(label):
    candidates = []
    for entry in Path("/sys/class/block").iterdir():
        if (entry / "partition").is_file() and f"PARTNAME={label}" in (entry / "uevent").read_text().splitlines():
            candidates.append(entry)
    require(len(candidates) == 1, f"Expected exactly one {label} partition")
    entry, = candidates
    return Path("/dev") / entry.name, int((entry / "partition").read_text())


def slot_flags(disk, number):
    match = re.search(r"(?m)^Attribute flags: ([0-9A-Fa-f]{16})$", run("sgdisk", "-i", str(number), str(disk)))
    require(match, "Could not read boot_a attributes")
    return int(match[1], 16)


def read_table(disk):
    return json.loads(run("sfdisk", "--json", str(disk)))


def without_boot_attrs(table, boot):
    for item in table["partitiontable"]["partitions"]:
        if item["node"] == str(boot):
            require(item.get("name") == "boot_a", "GPT entry does not match boot_a")
            item.pop("attrs", None)
            break
    else:
        raise ValueError("No boot_a entry in inspected GPT")
    return table


def verify_gpt(disk):
    require("No problems found." in run("sgdisk", "--verify", str(disk)),
            "Primary/backup GPT verification failed")


def inspect():
    require(os.geteuid() == 0, "Run under sudo")
    compatible = Path("/sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
    require(b"lenovo,tb710fu" in compatible, "Unsupported device")
    cmdline = Path("/proc/cmdline").read_text().split()
    require("androidboot.slot_suffix=_a" in cmdline and "tb710fu.install=single" not in cmdline,
            "Booted Ubuntu slot A is required")
    root, fs, options = run("findmnt", "-n", "-o", "SOURCE,FSTYPE,OPTIONS", "--target", "/").strip().split()
    arch, _ = partition("arch")
    require(Path(root).resolve() == arch and fs == "ext4" and "rw" in options.split(","),
            "Expected the installed ext4 arch partition mounted read-write as /")
    boot, number = partition("boot_a")
    parent = run("lsblk", "-ndo", "PKNAME", str(boot)).strip()
    require(re.fullmatch(r"sd[a-z]+", parent), "Unexpected boot_a backing disk")
    disk = Path("/dev") / parent
    require(int(run("blockdev", "--getss", str(disk))) == 4096, "Unexpected UFS logical sector size")
    require(int(run("blockdev", "--getsize64", str(boot))) == BOOT_BYTES, "Unexpected boot_a size")
    verify_gpt(disk)
    return boot, disk, number


def backup_gpt(disk):
    with tempfile.TemporaryDirectory(dir=STATE, prefix=".gpt-") as tmp:
        staged = Path(tmp) / "backup.gpt"
        run("sgdisk", "--backup=" + str(staged), str(disk))
        staged.chmod(0o600)
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(staged, STATE / "boot-gpt.before.bin")
        fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


GPT = struct.Struct("<8sIIIIQQQQ16sQIII")
SECTOR = 4096
ENTRY_SIZE = 128
MAX_TABLE = 1 << 20


def read_exact(fd, offset, size):
    value = os.pread(fd, size, offset)
    require(len(value) == size, "Short UFS GPT read")
    return value


def read_copy(fd, size, lba, number):
    header_offset = lba * SECTOR
    raw = read_exact(fd, header_offset, SECTOR)
    fields = GPT.unpack_from(raw)
    require(fields[0] == b"EFI PART" and fields[5] == lba and GPT.size <= fields[2] <= SECTOR,
            "Invalid UFS GPT header")
    updated = bytearray(raw)
    struct.pack_into("<I", updated, 16, 0)
    require(zlib.crc32(updated[:fields[2]]) == fields[3], "UFS GPT header check failed")
    table_offset = fields[10] * SECTOR
    table_size = fields[11] * fields[12]
    require(fields[12] == ENTRY_SIZE and 1 <= number <= fields[11] and
            0 < table_size <= MAX_TABLE and table_offset >= 2 * SECTOR and
            table_offset + table_size <= size and
            (header_offset + SECTOR <= table_offset or table_offset + table_size <= header_offset),
            "Unsupported UFS GPT table placement")
    table = read_exact(fd, table_offset, table_size)
    require(zlib.crc32(table) == fields[13], "UFS GPT entries check failed")
    return fields, raw, table


def proposals(fd, size, number, flags):
    require(size >= 4 * SECTOR and size % SECTOR == 0, "Unexpected UFS size")
    last = size // SECTOR - 1
    main = read_copy(fd, size, 1, number)
    alternate = read_copy(fd, size, last, number)
    require(main[0][6] == last and alternate[0][6] == 1 and
            main[0][7:10] == alternate[0][7:10] and main[1] != alternate[1] and
            main[2] == alternate[2], "Primary/backup UFS GPT copies differ")
    table = bytearray(main[2])
    entry = (number - 1) * ENTRY_SIZE
    attr = entry + 48
    require(table[entry+56:entry+128].decode("utf-16le").split("\0", 1)[0] == "boot_a" and
            struct.unpack_from("<Q", table, attr)[0] == flags, "Unexpected boot_a entry before write")
    struct.pack_into("<Q", table, attr, flags | SUCCESS)
    offset = attr // SECTOR * SECTOR
    updates = []
    for fields, raw, previous_table in (alternate, main):
        sector_offset = fields[10] * SECTOR + offset
        previous_sector = read_exact(fd, sector_offset, SECTOR)
        sector = bytearray(previous_sector)
        local_attr = attr - offset
        struct.pack_into("<Q", sector, local_attr, flags | SUCCESS)
        require(sector[:min(SECTOR, len(table) - offset)] == table[offset:offset+SECTOR] and
                previous_sector[:min(SECTOR, len(table) - offset)] == previous_table[offset:offset+SECTOR],
                "UFS entry sector changed before write")
        new_header = bytearray(raw)
        struct.pack_into("<I", new_header, 88, zlib.crc32(table))
        struct.pack_into("<I", new_header, 16, 0)
        struct.pack_into("<I", new_header, 16, zlib.crc32(new_header[:fields[2]]))
        updates.append(((sector_offset, previous_sector, bytes(sector)),
                        (fields[5] * SECTOR, raw, bytes(new_header))))
    require(updates[0][0][0] != updates[1][0][0], "UFS GPT copies share an entry sector")
    return updates


def write_verified(fd, offset, chunk):
    require(os.pwrite(fd, chunk, offset) == len(chunk), "Short UFS GPT sector write")
    os.fsync(fd)
    require(read_exact(fd, offset, len(chunk)) == chunk, "UFS GPT sector did not read back correctly")


def set_success(disk, number, flags):
    size = int(run("blockdev", "--getsize64", str(disk)))
    fd = os.open(disk, os.O_RDWR | os.O_NOFOLLOW)
    changed = []
    try:
        for pair in proposals(fd, size, number, flags):
            for offset, original, updated in pair:
                require(read_exact(fd, offset, SECTOR) == original, "UFS GPT changed before write")
                changed.append((offset, original))
                write_verified(fd, offset, updated)
    except (OSError, ValueError):
        try:
            for offset, original in reversed(changed):
                write_verified(fd, offset, original)
        except (OSError, ValueError):
            raise RuntimeError("Could not restore all changed sectors; keep the local GPT backup") from None
        raise
    finally:
        os.close(fd)


def read_boot_hash(boot):
    with boot.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def mark(boot, disk, number, flags):
    require(flags & ACTIVE and not flags & UNBOOTABLE, "A is not the usable active slot")
    if flags & SUCCESS:
        return False
    require(os.uname().release == KERNEL, "Running kernel is not the BOOTFIX release")
    require(read_boot_hash(boot) == BOOT_SHA, "boot_a does not match the BOOTFIX boot image")
    before = without_boot_attrs(read_table(disk), boot)
    backup_gpt(disk)
    require(slot_flags(disk, number) == flags, "boot_a flags changed during inspection")
    require(without_boot_attrs(read_table(disk), boot) == before, "GPT changed during inspection")

    set_success(disk, number, flags)
    verify_gpt(disk)
    require(slot_flags(disk, number) == flags | SUCCESS, "boot_a flags did not verify")
    require(without_boot_attrs(read_table(disk), boot) == before, "Other GPT entries differ from the backup")
    return True


def describe(flags):
    print("A slot: "
          f"retry={(flags & RETRIES) >> 51} "
          f"successful={'yes' if flags & SUCCESS else 'no'} "
          f"unbootable={'yes' if flags & UNBOOTABLE else 'no'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Set boot-successful after validating the installed Ubuntu")
    args = parser.parse_args()
    try:
        boot, disk, number = inspect()
        flags = slot_flags(disk, number)
        describe(flags)
        if args.apply:
            STATE.mkdir(mode=0o700, exist_ok=True)
            fd = os.open(STATE / "lock", os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                flags = slot_flags(disk, number)
                if mark(boot, disk, number, flags):
                    describe(slot_flags(disk, number))
            finally:
                os.close(fd)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"BOOTFIX slot confirmation: {error}\n")


if __name__ == "__main__":
    main()
