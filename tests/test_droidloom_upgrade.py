import hashlib
import io
import tarfile
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "contributions/droidloom-upgrade/attachments/upgrade.py"
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
                if release in (42, 48, 49, 50):
                    self.assertEqual(upgrade.upstream.installed_version(file), f"0.1.0-{release}")
                else:
                    with self.assertRaises(ValueError):
                        upgrade.upstream.installed_version(file)

    def test_release_49_apply_skips_package_download_and_service_changes(self):
        with (
            patch.object(upgrade, "host_check"),
            patch.object(upgrade.upstream, "installed_version", return_value="0.1.0-49"),
            patch.object(upgrade.upstream, "latest", return_value=("0.1.0-49", [])),
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
            state = upgrade.snapshot([original, extra], 1000, 42, False, "0.1.0-50")
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
            upgrade.snapshot([existing], 1000, 48, False, "0.1.0-50")
        self.assertFalse(backup.exists())
        self.assertTrue(existing.exists())

    def test_later_image_can_reuse_an_older_base(self):
        def index(base_dependency):
            buffer = io.BytesIO()
            with tarfile.open(mode="w:gz", fileobj=buffer) as archive:
                for name, version, dependency in [
                    ("droidloom-runtime", "0.2.0-1", "droidloom-image=0.2.0-1"),
                    ("droidloom-image", "0.2.0-1", base_dependency),
                    ("droidloom-base", "0.1.0-49", ""),
                ]:
                    fields = {"NAME": name, "VERSION": version, "ARCH": "aarch64",
                              "FILENAME": f"{name}-{version}-aarch64.pkg.tar.zst",
                              "CSIZE": "256", "SHA256SUM": "a" * 64, "DEPENDS": dependency}
                    raw = "\n\n".join(f"%{k}%\n{v}" for k, v in fields.items()).encode()
                    member = tarfile.TarInfo(f"{name}-{version}/desc")
                    member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
            return buffer.getvalue()
        target, packages = upgrade.upstream.read_index(index("droidloom-base=0.1.0-49"))
        self.assertEqual(target, "0.2.0-1")
        self.assertEqual(packages[-1][0], "droidloom-base-0.1.0-49-aarch64.pkg.tar.zst")
        with self.assertRaisesRegex(ValueError, "依赖"):
            upgrade.upstream.read_index(index("droidloom-base=0.2.0-1"))

    def test_next_upgrade_rolls_back_to_previous_version(self):
        file = self.root / "runtime"
        backup = self.root / "backup"
        with (patch.object(upgrade, "BACKUP", backup),
              patch.object(upgrade, "stop"), patch.object(upgrade, "reload_units")):
            file.write_text("49")
            upgrade.snapshot([file], 1000, "0.1.0-49", False, "0.1.0-50")
            file.write_text("50")
            (backup / "completed").touch()
            state = upgrade.snapshot([file], 1000, "0.1.0-50", False, "0.2.0-1")
            file.write_text("0.2.0-1")
            upgrade.restore((), state)
            self.assertEqual(file.read_text(), "50")
            self.assertEqual(len(list(self.root.glob("backup-*"))), 1)

    def test_wrong_bytes_with_correct_size_are_rejected(self):
        asset = self.root / "asset"
        asset.write_bytes(b"wrong")
        self.assertFalse(upgrade.valid_archive(asset, 5, hashlib.sha256(b"valid").hexdigest()))
        self.assertTrue(upgrade.valid_archive(asset, 5, hashlib.sha256(b"wrong").hexdigest()))


if __name__ == "__main__":
    unittest.main()
