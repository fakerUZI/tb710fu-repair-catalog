import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ASSETS = Path(__file__).resolve().parents[1] / "contributions/ubuntu-uboot-uefi/attachments"
spec = importlib.util.spec_from_file_location("targets", ASSETS / "boot_targets.py")
targets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(targets)


class BootTargetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sys = self.root / "class-block"
        self.dev = self.root / "dev"
        self.sys.mkdir()
        self.dev.mkdir()
        self.host = self.root / "host3"
        driver = self.host / "scsi_host/host3/proc_name"
        driver.parent.mkdir(parents=True)
        driver.write_text("ufshcd\n")
        self.tables = {}
        self.arch = self.partition(self.host, "sdh", 19, "arch")
        self.boot = self.partition(self.host, "sdk", 4, "boot_a")
        self.flags = (1 << 50) | (1 << 54)
        self.enterContext(patch.object(targets, "SYS_BLOCK", self.sys))
        self.enterContext(patch.object(targets, "DEV", self.dev))
        self.enterContext(patch.object(targets.stat, "S_ISBLK", return_value=True))
        self.enterContext(patch.object(targets, "run", side_effect=self.command))

    def partition(self, host, disk, number, label):
        name = disk + str(number)
        node = self.dev / name
        node.touch()
        real = host / "block" / disk / name
        real.mkdir(parents=True)
        (self.sys / name).symlink_to(real)
        for key, value in dict(partition=number, start=8000, size=196608, uevent=f"PARTNAME={label}").items():
            (real / key).write_text(str(value))
        self.tables.setdefault(disk, []).append(dict(name=label, node=str(node), start=1000, size=24576))
        return node

    def command(self, *args):
        if args[0] == "findmnt":
            return f"{self.arch} ext4 /"
        if args[0] == "sfdisk":
            self.assertIn(args[-1].name, ("sdh", "sdk"))
            return json.dumps(dict(partitiontable=dict(label="gpt", unit="sectors", sectorsize=4096,
                                                       partitions=self.tables[args[-1].name])))
        if args[0] == "blockdev":
            return str(96 << 20)
        self.assertEqual(args, ("sgdisk", "-i", "4", self.dev / "sdk"))
        return f"Attribute flags: {self.flags:016x}"

    def test_external_duplicate_labels_and_other_ufs_luns_do_not_gate(self):
        for label, number in [("arch", 1), ("boot_a", 2)]:
            self.partition(self.root / "usb-host", "sda", number, label)
        self.partition(self.host, "sdj", 2, "unused")
        self.assertEqual(targets.checked_boot(96 << 20), self.boot)

    def test_internal_ambiguity_refused(self):
        self.partition(self.host, "sdj", 2, "boot_a")
        with self.assertRaisesRegex(ValueError, "重名"):
            targets.locate_targets()

    def test_overlap_with_target_refused(self):
        self.tables["sdk"].append(dict(name="other", node="unused", start=1001, size=8))
        with self.assertRaisesRegex(ValueError, "重叠"):
            targets.locate_targets()

    def test_external_root_refused(self):
        self.arch = self.partition(self.root / "usb-host", "sda", 1, "arch")
        with self.assertRaisesRegex(ValueError, "内置 UFS"):
            targets.locate_targets()

    def test_changed_target_geometry_refused(self):
        self.tables["sdk"][0]["start"] += 1
        with self.assertRaisesRegex(ValueError, "分区范围"):
            targets.checked_boot(96 << 20)

    def test_inactive_slot_and_wrong_boot_size_refused(self):
        with self.assertRaisesRegex(ValueError, "大小"):
            targets.checked_boot(128 << 20)
        self.flags = 1 << 54
        with self.assertRaisesRegex(ValueError, "A 槽"):
            targets.checked_boot(96 << 20)
