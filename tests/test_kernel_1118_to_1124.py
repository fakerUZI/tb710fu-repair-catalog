import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stdout
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

    def test_all_firmware_can_differ_and_read_errors_still_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            devices = {name: Path(tmp) / name for name in profile["firmware"]}
            for name, path in devices.items():
                path.write_bytes(f"another firmware build: {name}".encode())
            with redirect_stdout(io.StringIO()) as output:
                kernel.inspect_firmware(devices, profile["firmware"])
            self.assertEqual(output.getvalue().strip().split("：")[-1].split("、"), list(devices))
            matching = {name: kernel.digest(path) for name, path in devices.items()}
            with redirect_stdout(io.StringIO()) as output:
                kernel.inspect_firmware(devices, matching)
            self.assertEqual(output.getvalue(), "")
            devices["abl_a"].unlink()
            with self.assertRaisesRegex(ValueError, "无法读取固件分区 abl_a"):
                kernel.inspect_firmware(devices, profile["firmware"])

    def test_unknown_boot_still_stops_before_install(self):
        with patch.object(kernel, "inspect", return_value=(Path("/unused"), "0" * 64)), \
             patch.object(kernel.platform, "release", return_value=kernel.OLD), \
             patch.object(kernel, "install") as install:
            with self.assertRaisesRegex(ValueError, "1118 kShim"):
                kernel.execute("apply", profile)
            install.assert_not_called()

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
