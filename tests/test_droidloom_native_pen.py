import gzip
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "contributions/droidloom-native-pen/attachments/install.py"
spec = importlib.util.spec_from_file_location("native_pen", SCRIPT)
pen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pen)


class InstallTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.assets = root / "assets"
        self.assets.mkdir()
        (self.assets / "install.py").write_bytes(SCRIPT.read_bytes())
        self.live = root / "runtime" / pen.GUEST
        self.live.parent.mkdir()
        self.live.write_bytes(b"official-original")
        self.account = SimpleNamespace(pw_uid=1000)
        self.release = {}
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = b"\xb7\x00"
        for name in (pen.HOST, pen.GUEST):
            payload = header + (pen.MARKER if name == pen.GUEST else b"presenter")
            data = gzip.compress(payload)
            half = len(data) // 2
            (self.assets / (name + ".gz.001")).write_bytes(data[:half])
            (self.assets / (name + ".gz.002")).write_bytes(data[half:])
            self.release[name] = dict(parts=2, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        self.enterContext(patch.multiple(pen, ASSETS=self.assets, ROOT=root / "installed",
                                         LIVE=self.live, FACTORY_SHA=pen.digest(self.live)))
        self.desktop = self.enterContext(patch.object(pen, "desktop_action"))
        self.matches = self.enterContext(patch.object(pen, "release_matches", return_value=True))

    def run_action(self, action):
        pen.apply_or_rollback(self.account, action, self.release)

    def test_apply_repeat_and_rollback(self):
        self.run_action("apply")
        self.run_action("apply")
        self.assertEqual(pen.digest(self.live), self.release[pen.GUEST]["sha256"])
        self.run_action("rollback")
        self.assertEqual(pen.digest(self.live), pen.FACTORY_SHA)
        self.assertFalse(pen.ROOT.exists())
        self.desktop.assert_called_with(self.account, "disable")

    def test_missing_guest_part_leaves_original(self):
        (self.assets / (pen.GUEST + ".gz.002")).unlink()
        with self.assertRaises(ValueError):
            self.run_action("apply")
        self.assertFalse(pen.ROOT.exists())
        self.assertEqual(pen.digest(self.live), pen.FACTORY_SHA)
        self.desktop.assert_not_called()

    def test_rejected_reload_restores_library_and_can_retry(self):
        self.desktop.side_effect = [RuntimeError("reload failed"), None, None]
        with self.assertRaisesRegex(RuntimeError, "reload failed"):
            self.run_action("apply")
        self.assertEqual(pen.digest(self.live), pen.FACTORY_SHA)
        self.run_action("apply")
        self.assertEqual(pen.digest(self.live), self.release[pen.GUEST]["sha256"])

    def test_rollback_after_system_upgrade_preserves_new_library(self):
        self.run_action("apply")
        self.live.write_bytes(b"new system software")
        self.matches.return_value = False
        self.run_action("rollback")
        self.assertEqual(self.live.read_bytes(), b"new system software")
        self.assertFalse(pen.ROOT.exists())


if __name__ == "__main__":
    unittest.main()
