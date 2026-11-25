import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "kernel-1118-to-1124"
ASSETS = ROOT / "contributions" / ENTRY / "attachments"
spec = importlib.util.spec_from_file_location("kernel1124", ASSETS / "upgrade.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)
profile = json.loads((ASSETS / "profile.json").read_bytes())


class Kernel1124Tests(unittest.TestCase):
    def test_separate_entries_and_download_limits(self):
        entries = {i["id"]: i for i in json.loads((ROOT / "catalog.json").read_bytes())["issues"]}
        for name in (ENTRY, "ubuntu-uboot-uefi"):
            self.assertEqual(entries[name]["status"], "manual-install")
        self.assertEqual(profile["boot_after"], json.loads((ROOT / "contributions/ubuntu-uboot-uefi/attachments/profile.json").read_bytes())["previous_boot"])
        for info in [entries[ENTRY]["script"], *entries[ENTRY]["attachments"]]:
            f = ROOT / info["path"]
            self.assertLess(f.stat().st_size, 10 << 20)
            self.assertEqual(kernel.digest(f), info["sha256"])
        parts = hashlib.sha256()
        for name in profile["parts"]:
            parts.update((ASSETS / name).read_bytes())
        self.assertEqual(parts.hexdigest(), profile["archive"])

    def test_already_written_and_pending_reboot_skip_install(self):
        for running in (kernel.NEW, kernel.OLD):
            with self.subTest(running=running), patch.object(kernel.platform, "release", return_value=running), \
                 patch.object(kernel, "inspect", return_value=(None, profile["boot_after"])), \
                 patch.object(Path, "is_file", return_value=True), patch.object(kernel, "install") as install:
                kernel.execute("apply", profile)
                install.assert_not_called()

    def test_uefi_must_be_rolled_back_separately(self):
        with patch.object(kernel, "inspect", return_value=(None, profile["uefi"])), \
             patch.object(kernel, "write_boot") as flash:
            with self.assertRaisesRegex(ValueError, "U-Boot"):
                kernel.execute("rollback", profile)
            flash.assert_not_called()

    def test_refuse_to_replace_a_later_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "helper"
            target.write_bytes(b"old")
            before = kernel.digest(target)
            target.write_bytes(b"new")
            after = kernel.digest(target)
            target.write_bytes(b"user edit")
            with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                kernel.restore_shared({"files": [dict(target=str(target), before=before, after=after, mode=0o755)]})
            self.assertEqual(target.read_bytes(), b"user edit")
