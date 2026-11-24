from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ATTACHMENTS = Path(__file__).resolve().parents[1] / "contributions/pogo-keyboard-trial/attachments"
sys.path.insert(0, str(ATTACHMENTS))
try:
    import pen_install as pen
    import pogo_pen_install as bundle
finally:
    sys.path.pop(0)


class CombinedRepairTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.assets = root / "attachments"
        self.modroot = root / "lib/modules" / pen.KERNEL
        self.state = root / "var/lib/tb710fu-pogo-pen"
        self.modules = {}
        self.new_modules = {}
        profiles = {}
        for target, (profile, _) in pen.PROFILES.items():
            old = self.modroot / target
            new = self.assets / "driver/pen" / profile / pen.DRIVER
            for file, content in ((old, profile + "-before"), (new, profile + "-after")):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
            profiles[target] = profile, pen.sha(old)
            self.modules[profile] = old
            self.new_modules[profile] = new
        self.current = self.modules["stock"]
        self.enterContext(patch.multiple(pen, ROOT=self.assets, MODROOT=self.modroot,
                                        STATE=self.state, PROFILES=profiles))
        self.release = self.enterContext(patch.object(pen.platform, "release", return_value=pen.KERNEL))
        self.enterContext(patch.object(pen, "modinfo", side_effect=self.modinfo))
        self.depmod = self.enterContext(patch.object(pen, "depmod"))
        self.pogo_check = self.enterContext(patch.object(bundle.pogo_install, "check_install"))
        self.pogo_installed = self.enterContext(patch.object(bundle.pogo_install, "installed_payload",
                                                              return_value=False))
        self.pogo_apply = self.enterContext(patch.object(bundle.pogo_install, "apply"))
        self.pogo_rollback = self.enterContext(patch.object(bundle.pogo_install, "rollback"))
        self.enterContext(redirect_stdout(StringIO()))

    def modinfo(self, field, _):
        return {"filename": str(self.current), "vermagic": pen.KERNEL + " SMP preempt",
                "name": "nvt_36xxx"}[field]

    def test_check_reads_without_creating_a_backup(self):
        bundle.check()
        self.assertFalse(self.state.exists())
        self.depmod.assert_not_called()
        self.pogo_check.assert_called_once()

    def test_apply_and_rollback_each_verified_profile(self):
        for profile, current in self.modules.items():
            with self.subTest(profile=profile):
                self.current = current
                before = current.read_bytes()
                bundle.apply()
                self.assertEqual(current.read_bytes(), self.new_modules[profile].read_bytes())
                self.assertEqual((self.state / "original.ko").read_bytes(), before)
                bundle.rollback()
                self.assertEqual(current.read_bytes(), before)
                self.assertFalse(self.state.exists())

    def test_reapply_preserves_original_backup(self):
        before = self.current.read_bytes()
        bundle.apply()
        bundle.apply()
        self.assertEqual((self.state / "original.ko").read_bytes(), before)
        self.depmod.assert_called_once()
        self.pogo_apply.assert_called()

    def test_rollback_keeps_separately_installed_keyboard(self):
        self.pogo_installed.return_value = True
        bundle.apply()
        bundle.rollback()
        self.pogo_rollback.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_pogo_start_error_restores_touch_module(self):
        before = self.current.read_bytes()
        self.pogo_apply.side_effect = subprocess.CalledProcessError(1, "systemctl")
        with self.assertRaises(subprocess.CalledProcessError):
            bundle.apply()
        self.assertEqual(self.current.read_bytes(), before)
        self.assertFalse(self.state.exists())

    def test_depmod_error_restores_touch_module_and_allows_retry(self):
        before = self.current.read_bytes()
        self.depmod.side_effect = [subprocess.CalledProcessError(1, "depmod"), None, None]
        with self.assertRaises(subprocess.CalledProcessError):
            bundle.apply()
        self.assertEqual(self.current.read_bytes(), before)
        self.pogo_apply.assert_not_called()
        bundle.apply()
        self.assertEqual(self.current.read_bytes(), self.new_modules["stock"].read_bytes())

    def test_unrecognized_module_stops_before_writes(self):
        self.current.write_text("independently installed touch driver")
        with self.assertRaisesRegex(RuntimeError, "触控模块不匹配"):
            bundle.apply()
        self.assertFalse(self.state.exists())
        self.pogo_apply.assert_not_called()

    def test_rollback_detects_a_later_module_change_before_stopping_keyboard(self):
        bundle.apply()
        self.current.write_text("updated externally")
        with self.assertRaisesRegex(RuntimeError, "触控模块不匹配"):
            bundle.rollback()
        self.pogo_rollback.assert_not_called()
        self.assertTrue(self.state.exists())

    def test_wrong_kernel_cannot_modify_keyboard_or_touch(self):
        self.release.return_value = "7.2.6-tb710fu-20261124"
        with self.assertRaisesRegex(RuntimeError, "手写笔测试模块适用于"):
            bundle.apply()
        with self.assertRaisesRegex(RuntimeError, "手写笔测试模块适用于"):
            bundle.rollback()
        self.pogo_apply.assert_not_called()
        self.pogo_rollback.assert_not_called()
        self.depmod.assert_not_called()


class ModuleIndexTests(unittest.TestCase):
    def test_depmod_keeps_indexes_readable_for_regular_users(self):
        with patch.object(pen.subprocess, "run") as run:
            pen.depmod()
        run.assert_called_once_with(["/usr/sbin/depmod", "-a", pen.KERNEL],
                                    check=True, timeout=60, umask=0o022)


if __name__ == "__main__":
    unittest.main()
