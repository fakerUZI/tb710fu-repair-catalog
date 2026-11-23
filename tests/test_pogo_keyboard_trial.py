from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ATTACHMENTS = Path(__file__).resolve().parents[1] / "contributions/pogo-keyboard-trial/attachments"
sys.path.insert(0, str(ATTACHMENTS))
try:
    import pogo_trial as pogo
finally:
    sys.path.pop(0)


class BindingTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.tree = root / "tree"
        self.node = self.tree / "gpio-keys"
        self.node.mkdir(parents=True)
        self.platform = root / "platform"
        self.platform.mkdir()
        self.driver = root / "drivers/gpio-keys"
        self.driver.mkdir(parents=True)
        for attr in ("bind", "unbind"):
            (self.driver / attr).touch()
        self.enterContext(patch.multiple(pogo, PLATFORM_DEVICES=self.platform, GPIO_DRIVER=self.driver))
        self.trial = pogo.Trial()

    def platform_device(self, name, node, driver=None):
        path = self.platform / name
        path.mkdir()
        (path / "of_node").symlink_to(node)
        if driver is not None:
            (path / "driver").symlink_to(driver)
        return path

    def test_detects_device_by_of_node_instead_of_platform_name(self):
        other = self.tree / "other"
        other.mkdir()
        self.platform_device("gpio-keys", other, self.driver)
        matched = self.platform_device("gpio-keys.0", self.node, self.driver)
        self.trial.find_gpio_keys(self.tree)
        self.assertEqual(self.trial.gpio_device, matched)
        self.assertTrue(self.trial.original_gpio_bound)

    def test_unbound_platform_device_is_valid(self):
        self.platform_device("board-hall-keys", self.node)
        self.trial.find_gpio_keys(self.tree)
        self.assertIs(self.trial.original_gpio_bound, False)

    def test_rejects_a_device_managed_by_another_driver(self):
        other = self.driver.parent / "other"
        other.mkdir()
        self.platform_device("gpio-keys.2", self.node, other)
        with self.assertRaisesRegex(RuntimeError, "由 other 驱动"):
            self.trial.find_gpio_keys(self.tree)

    def test_reports_absent_or_ambiguous_devices(self):
        with self.assertRaisesRegex(RuntimeError, "数量为 0"):
            self.trial.find_gpio_keys(self.tree)
        self.platform_device("gpio-keys.0", self.node)
        self.platform_device("gpio-keys.1", self.node)
        with self.assertRaisesRegex(RuntimeError, "数量为 2"):
            self.trial.find_gpio_keys(self.tree)

    def test_reports_disabled_node_and_absent_driver(self):
        status = self.node / "status"
        status.write_bytes(b"disabled\0")
        with self.assertRaisesRegex(RuntimeError, "禁用状态"):
            self.trial.find_gpio_keys(self.tree)
        status.unlink()
        self.platform_device("gpio-keys", self.node)
        (self.driver / "bind").unlink()
        with self.assertRaisesRegex(RuntimeError, "未注册可重绑"):
            self.trial.find_gpio_keys(self.tree)

    def test_partial_load_restores_either_original_binding_state(self):
        for original in (False, True):
            with self.subTest(initially_bound=original):
                started = pogo.Trial()
                started.original_gpio_bound = original
                state = original
                actions = []

                def set_bound(wanted):
                    nonlocal state
                    if state != wanted:
                        actions.append(("bind" if wanted else "unbind",))
                        state = wanted

                def command(argv, *, check):
                    actions.append((argv[0], Path(argv[1]).stem))
                    if argv[0] == "insmod" and Path(argv[1]).stem == "lenovo_keyboard":
                        raise subprocess.CalledProcessError(1, argv)

                firmware = self.platform / "firmware"
                with (
                    patch.object(started, "gpio_bound", side_effect=lambda: state),
                    patch.object(started, "set_gpio_bound", side_effect=set_bound),
                    patch.object(started, "build_firmware", return_value=b"fixture"),
                    patch.object(pogo, "FIRMWARE", firmware),
                    patch.object(pogo.subprocess, "run", side_effect=command),
                ):
                    with self.assertRaises(subprocess.CalledProcessError):
                        try:
                            started.start("fixture")
                        finally:
                            started.stop()
                self.assertIs(state, original)
                self.assertFalse(firmware.exists())
                self.assertIn(("rmmod", "tb_pogo_overlay"), actions)
                self.assertNotIn(("rmmod", "lenovo_keyboard"), actions)


if __name__ == "__main__":
    unittest.main()
