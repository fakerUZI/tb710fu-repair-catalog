import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "contributions/suspend-restore/attachments/install.py"
spec = importlib.util.spec_from_file_location("suspend_restore", SOURCE)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class SuspendRestoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.config = self.root / "etc/sleep.conf.d/99-tb710fu-s2idle.conf"
        self.system = self.root / "etc/system"
        self.runtime = self.root / "run/system"
        self.state = self.root / "before.json"
        self.system.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.calls = []
        self.override = ""
        self.logind_block = False
        self.enterContext(patch.multiple(repair, CONFIG=self.config, UNIT_DIRS=(self.system, self.runtime),
                                         run=self.run_system))

    def run_system(self, *args):
        self.calls.append(args)
        if args[:2] == ("systemctl", "show"):
            return "masked" if any(repair.is_mask(d / args[2]) for d in repair.UNIT_DIRS) else "loaded"
        if args[0] == "systemd-analyze":
            settings = self.config.read_text() if self.config.exists() else ""
            return "[Sleep]\n#MemorySleepMode=\n" + settings + self.override
        if args[0] == "busctl":
            enabled = not self.logind_block and not any(repair.is_mask(path) for path in repair.unit_paths())
            return 's "challenge"' if enabled else 's "no"'
        self.assertEqual(args, ("systemctl", "daemon-reload"))
        return ""

    def test_apply_repeat_and_restore_exact_masks(self):
        expected_masks = [self.system / "sleep.target", self.runtime / "suspend.target"]
        hibernate = self.system / "hibernate.target"
        for mask in [*expected_masks, hibernate]:
            mask.symlink_to("/dev/null")

        repair.apply(self.state)
        self.assertTrue(repair.effective_s2idle())
        self.assertTrue(self.state.is_file())
        self.assertFalse(any(repair.present(path) for path in expected_masks))
        saved = self.state.read_bytes()
        repair.apply(self.state)
        self.assertEqual(saved, self.state.read_bytes())
        repair.recover(repair.read_state(self.state), self.state)
        self.assertTrue(all(repair.is_mask(path) for path in [*expected_masks, hibernate]))
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())

    def test_same_configuration_on_live_style_image_is_no_op(self):
        self.config.parent.mkdir(parents=True)
        previous = "[Sleep]\nSuspendState=mem\nMemorySleepMode=s2idle\n"
        self.config.write_text(previous)
        repair.apply(self.state)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.config.read_text(), previous)
        self.assertNotIn(("systemctl", "daemon-reload"), self.calls)

    def test_failed_logind_verification_restores_original_state(self):
        mask = self.system / "suspend.target"
        mask.symlink_to("/dev/null")
        self.logind_block = True
        with self.assertRaisesRegex(ValueError, "logind"):
            repair.apply(self.state)
        self.assertTrue(repair.is_mask(mask))
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())

    def test_late_override_prevents_unmasking_and_restores_prior_state(self):
        mask = self.system / "sleep.target"
        mask.symlink_to("/dev/null")
        self.override = "[Sleep]\nMemorySleepMode=deep\n"
        with self.assertRaisesRegex(ValueError, "覆盖"):
            repair.apply(self.state)
        self.assertTrue(repair.is_mask(mask))
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())

    def test_rollback_preserves_later_administrator_changes(self):
        mask = self.system / "sleep.target"
        mask.symlink_to("/dev/null")
        repair.apply(self.state)
        self.config.write_text("[Sleep]\nMemorySleepMode=deep\n")
        with self.assertRaisesRegex(ValueError, "另有改动"):
            repair.recover(repair.read_state(self.state), self.state)
        self.assertFalse(repair.present(mask))
        self.assertTrue(self.state.is_file())
        self.assertIn("deep", self.config.read_text())
