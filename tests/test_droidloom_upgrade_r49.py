import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "contributions/droidloom-upgrade-r49/attachments/upgrade.py"
SPEC = importlib.util.spec_from_file_location("droidloom_upgrade", SCRIPT)
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)

    def test_release_gate(self):
        file = self.root / "package.json"
        for release in (42, 48, 49, 28, 50, "49"):
            file.write_text(json.dumps({"version": "0.1.0", "architecture": "aarch64", "release": release}))
            with self.subTest(release=release):
                if release in (42, 48, 49):
                    self.assertEqual(upgrade.release_at(file), release)
                else:
                    with self.assertRaises(ValueError):
                        upgrade.release_at(file)

    def test_release_49_apply_skips_package_download_and_service_changes(self):
        with (
            patch.object(upgrade, "host_check"),
            patch.object(upgrade, "release_at", return_value=49),
            patch.object(upgrade.os, "geteuid", return_value=0),
            patch("sys.argv", ["upgrade.py", "apply"]),
            patch("builtins.open", return_value=(self.root / "lock").open("a")),
            patch.object(upgrade, "apply") as apply,
            patch.object(upgrade, "desktop") as desktop,
        ):
            upgrade.main()
        apply.assert_not_called()
        desktop.assert_not_called()

    def test_snapshot_and_restore_restore_existing_files_and_remove_added_files(self):
        original = self.root / "layout" / "existing"
        extra = original.with_name("new")
        original.parent.mkdir()
        original.write_bytes(b"old")
        backup = self.root / "backup"
        with (
            patch.object(upgrade, "BACKUP", backup),
            patch.object(upgrade, "stop"),
            patch.object(upgrade, "reload_units"),
        ):
            state = upgrade.snapshot([original, extra], 1000, 42, False)
            original.write_bytes(b"new")
            extra.write_bytes(b"added")
            (backup / "completed").touch()
            upgrade.restore((), state)
        self.assertEqual(original.read_bytes(), b"old")
        self.assertFalse(extra.exists())
        self.assertFalse((backup / "completed").exists())
        self.assertFalse((backup / "state.json").exists())
        self.assertEqual(json.loads((backup / "restored.json").read_text())["release"], 42)

    def test_failed_snapshot_leaves_no_published_backup(self):
        existing = self.root / "existing"
        existing.touch()
        backup = self.root / "backup"
        with (
            patch.object(upgrade, "BACKUP", backup),
            patch.object(upgrade, "copy", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            upgrade.snapshot([existing], 1000, 48, False)
        self.assertFalse(backup.exists())
        self.assertTrue(existing.exists())

    def test_wrong_bytes_with_correct_size_are_rejected(self):
        asset = self.root / "asset"
        asset.write_bytes(b"wrong")
        self.assertFalse(upgrade.valid_archive(asset, 5, hashlib.sha256(b"valid").hexdigest()))
        self.assertTrue(upgrade.valid_archive(asset, 5, hashlib.sha256(b"wrong").hexdigest()))


if __name__ == "__main__":
    unittest.main()
