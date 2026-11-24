import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib


SPEC = importlib.util.spec_from_file_location("boot_slot", (Path(__file__).resolve().parents[1] / "contributions/bootfix-slot-success/attachments/tb710fu-boot-slot.py"))
SLOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SLOT)
FLAGS = 0x0037000000000000
LAST = 63
SECTOR = SLOT.SECTOR
SIZE = (LAST + 1) * SECTOR


def vendor_disk():
    table = bytearray(3 * SECTOR)
    for index, (name, flags) in enumerate((("boot_a", FLAGS), ("boot_b", 1 << 60))):
        entry = SLOT.ENTRY_SIZE * index
        struct.pack_into("<16s16sQQQ", table, entry, bytes([1+index]) * 16, bytes([3+index]) * 16,
                         8+index*10, 17+index*10, flags)
        label = name.encode("utf-16le")
        table[entry+56:entry+56+len(label)] = label
    disk = bytearray(SIZE)
    disk[450:454] = bytes([0xEE, 0, 0xC2, 0xFF])
    disk[510:512] = b"\x55\xaa"
    for header_lba, other_lba, entries_lba in ((1, LAST, 2), (LAST, 1, LAST - 4)):
        offset = header_lba * SECTOR
        SLOT.GPT.pack_into(disk, offset, b"EFI PART", 0x10000, SLOT.GPT.size, 0, 0,
                           header_lba, other_lba, 6, LAST - 5, b"D" * 16, entries_lba,
                           len(table) // SLOT.ENTRY_SIZE, SLOT.ENTRY_SIZE, zlib.crc32(table))
        struct.pack_into("<I", disk, offset + 16, zlib.crc32(disk[offset:offset + SLOT.GPT.size]))
        start = entries_lba * SECTOR
        disk[start:start + len(table)] = table
    return bytes(disk)


class VendorSlotTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.disk = Path(folder) / "vendor.img"
        self.initial = vendor_disk()
        self.disk.write_bytes(self.initial)
        self.enterContext(patch.object(SLOT, "run", return_value=str(SIZE)))

    def test_touches_only_success_byte_and_crc_fields(self):
        SLOT.set_success(self.disk, 1, FLAGS)
        modified = self.disk.read_bytes()
        allowed = {lba*SECTOR + i for lba in (1, LAST) for i in (16, 17, 18, 19, 88, 89, 90, 91)}
        for lba in (2, LAST - 4):
            bit_byte = lba * SECTOR + 54
            allowed.add(bit_byte)
            self.assertEqual(modified[bit_byte] ^ self.initial[bit_byte], 0x40)
        self.assertTrue({i for i, (a,b) in enumerate(zip(self.initial, modified)) if a != b} <= allowed)

    def test_wrong_slot_or_corrupted_crc_refuses_to_write(self):
        with self.assertRaises(ValueError):
            SLOT.set_success(self.disk, 2, 1 << 60)
        self.assertEqual(self.disk.read_bytes(), self.initial)
        bad = bytearray(self.initial)
        bad[2*SECTOR + 48] ^= 1
        self.disk.write_bytes(bad)
        with self.assertRaises(ValueError):
            SLOT.set_success(self.disk, 1, FLAGS)
        self.assertEqual(self.disk.read_bytes(), bad)

    def test_restores_both_copies_if_primary_write_fails(self):
        actual_write = SLOT.write_verified
        def fail_once(fd, offset, value):
            if offset == SECTOR and value != self.initial[SECTOR:2*SECTOR]:
                raise OSError("injected primary header failure")
            actual_write(fd, offset, value)
        with patch.object(SLOT, "write_verified", side_effect=fail_once):
            with self.assertRaises(OSError):
                SLOT.set_success(self.disk, 1, FLAGS)
        self.assertEqual(self.disk.read_bytes(), self.initial)


if __name__ == "__main__":
    unittest.main()
