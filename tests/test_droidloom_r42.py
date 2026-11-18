import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "contributions/droidloom-r42-game-compat/attachments/repair.py"
SPEC = importlib.util.spec_from_file_location("droidloom_r42", SCRIPT)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class DroidloomRepairTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)

    def test_distinguishes_fresh_update_and_current(self):
        version, binary, config = (self.root / name for name in ("version", "ctl", "spec"))
        with patch.multiple(repair, VERSION=version, CTL=binary, SPEC=config):
            self.assertIsNone(repair.package_state())
            binary.touch()
            with self.assertRaisesRegex(RuntimeError, "未知"):
                repair.package_state()
            repair.save(version, dict(repair.RELEASE, release=28))
            self.assertEqual(repair.package_state(), 28)
            repair.save(version, repair.RELEASE)
            self.assertEqual(repair.package_state(), 42)
            repair.save(version, dict(repair.RELEASE, release=43))
            with self.assertRaisesRegex(RuntimeError, "未知"):
                repair.package_state()

    def test_fresh_download_checks_size_digest_and_reuses_cache(self):
        cache = self.root / "cache"
        archive = self.root / "source"
        archive.write_bytes(b"verified archive")
        url = "https://example.invalid/release.tar.zst"
        def downloader(*args, **kwargs):
            args[args.index("--output") + 1].write_bytes(archive.read_bytes())
            return subprocess.CompletedProcess(args, 0)
        with patch.object(repair, "CACHE", cache), patch.object(repair, "run", side_effect=downloader) as command:
            saved = repair.fetch(url, archive.stat().st_size, repair.digest(archive))
            self.assertEqual(saved.read_bytes(), archive.read_bytes())
            self.assertEqual(repair.fetch(url, archive.stat().st_size, repair.digest(archive)), saved)
            self.assertEqual(command.call_count, 1)
            saved.unlink()
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                repair.fetch(url, archive.stat().st_size, "0" * 64)
            self.assertFalse(saved.exists())
            self.assertFalse(saved.with_suffix(".zst.part").exists())

    def test_snapshot_restores_existing_files_and_keeps_separate_data(self):
        backup = self.root / "backup"
        before, added, data = (self.root / name for name in ("existing", "added", "data"))
        before.write_text("old")
        data.write_text("untouched")
        state = {"uid": 1000, "running": False}
        with patch.multiple(repair, BACKUP=backup, STATE=backup / "state.json",
                            VERSION=self.root / "version"), patch.object(repair, "stop"), patch.object(repair, "reload_units"):
            repair.snapshot([before, added], state)
            before.write_text("updated")
            added.write_text("installed")
            repair.rollback(1000, (), state)
            self.assertEqual(before.read_text(), "old")
            self.assertFalse(added.exists())
            self.assertEqual(data.read_text(), "untouched")
            self.assertTrue((backup / "rolled-back.json").is_file())


if __name__ == "__main__":
    unittest.main()
