import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "ubuntu-uboot-uefi"
ASSETS = ROOT / "contributions" / ENTRY / "attachments"
PROFILE = json.loads((ASSETS / "profile.json").read_text())
spec = importlib.util.spec_from_file_location("uefi_migration", ASSETS / "migrate.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class UefiEntryTests(unittest.TestCase):
    def test_entry_requires_individual_install_and_all_files_match(self):
        issues = json.loads((ROOT / "catalog.json").read_bytes())["issues"]
        item, = [item for item in issues if item["id"] == ENTRY]
        self.assertEqual(item["status"], "manual-install")
        self.assertTrue(item["script"]["requires_admin"])
        for info in [item["script"], *item["attachments"]]:
            self.assertEqual(hashlib.sha256((ROOT / info["path"]).read_bytes()).hexdigest(), info["sha256"])
        self.assertEqual({ROOT / a["path"] for a in item["attachments"]},
                         {p for p in ASSETS.iterdir() if p.is_file()})

    def test_check_and_repeat_apply_never_download_or_write(self):
        for action, current in (("check", "previous_boot"), ("check", "uboot"), ("apply", "uboot")):
            with self.subTest(action=action, current=current), \
                 patch.object(migration, "preflight", return_value=(Path("/unused"), PROFILE[current])), \
                 patch.object(migration, "materialize") as build, patch.object(migration, "stage") as stage, \
                 patch.object(migration, "save_backup") as save, patch.object(migration, "switch") as switch:
                migration.migrate(action, PROFILE)
                for operation in (build, save, stage, switch):
                    operation.assert_not_called()

    def test_rollback_preflight_does_not_require_efi_files_or_handoff_reservations(self):
        self.enterContext(patch.object(migration.os, "geteuid", return_value=0))
        self.enterContext(patch.object(migration.platform, "release", return_value=migration.VERSION))
        self.enterContext(patch.object(migration.platform, "machine", return_value="aarch64"))
        self.enterContext(patch.object(Path, "read_text", return_value="androidboot.slot_suffix=_a"))
        def read_board(path):
            self.assertEqual(path, Path("/proc/device-tree/compatible"))
            return b"lenovo,tb710fu\0"
        self.enterContext(patch.object(Path, "read_bytes", read_board))
        boot = Path("/test/boot_a")
        self.enterContext(patch.object(migration.boot_targets, "checked_boot", return_value=boot))
        self.enterContext(patch.object(migration, "digest", return_value=PROFILE["uboot"]))
        self.assertEqual(migration.preflight(PROFILE, "rollback"), (boot, PROFILE["uboot"]))

    def test_failed_backup_stops_before_staging_and_flashing(self):
        with patch.object(migration, "preflight", return_value=(Path("/unused"), PROFILE["previous_boot"])), \
             patch.object(migration, "materialize"), patch.object(Path, "exists", return_value=False), \
             patch.object(migration, "save_backup", side_effect=ValueError("backup")), \
             patch.object(migration, "stage") as stage, patch.object(migration, "switch") as switch:
            with self.assertRaisesRegex(ValueError, "backup"):
                migration.migrate("apply", PROFILE)
            stage.assert_not_called()
            switch.assert_not_called()
