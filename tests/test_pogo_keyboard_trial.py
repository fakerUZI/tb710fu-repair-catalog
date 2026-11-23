from contextlib import nullcontext, redirect_stdout
import errno
from io import StringIO
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ATTACHMENTS = Path(__file__).resolve().parents[1] / "contributions/pogo-keyboard-trial/attachments"
sys.path.insert(0, str(ATTACHMENTS))
try:
    import pogo_trial as pogo
    import pogo_install as install
    from gnome_shortcuts import GnomeShortcuts
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

    def test_unbound_platform_device_needs_no_manual_binding(self):
        self.platform_device("board-hall-keys", self.node)
        (self.driver / "bind").unlink()
        (self.driver / "unbind").unlink()
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
        self.platform_device("gpio-keys", self.node, self.driver)
        (self.driver / "bind").unlink()
        with self.assertRaisesRegex(RuntimeError, "不支持重绑"):
            self.trial.find_gpio_keys(self.tree)

    def test_binding_retries_deferred_probe_without_hiding_permanent_error(self):
        device = self.platform_device("gpio-keys", self.node)
        self.trial.find_gpio_keys(self.tree)
        driver_link = device / "driver"
        attempts = []

        def bind(path, name):
            self.assertEqual(path, self.driver / "bind")
            self.assertEqual(name, device.name)
            attempts.append(name)
            if len(attempts) == 1:
                raise BlockingIOError(errno.EAGAIN, "deferred probe")
            driver_link.symlink_to(self.driver)

        with (
            patch.object(Path, "write_text", autospec=True, side_effect=bind),
            patch.object(pogo.time, "sleep") as delay,
        ):
            self.trial.set_gpio_bound(True)
        self.assertTrue(self.trial.gpio_bound())
        self.assertEqual(len(attempts), 2)
        delay.assert_called_once()
        driver_link.unlink()
        with (
            patch.object(Path, "write_text", side_effect=OSError(errno.EBUSY, "busy")) as write,
            patch.object(self.trial, "report_probe_failure") as report,
            self.assertRaisesRegex(RuntimeError, "busy"),
        ):
            self.trial.set_gpio_bound(True)
        write.assert_called_once()
        report.assert_called_once()

    def test_deferred_probe_timeout_reports_only_relevant_devices(self):
        self.platform_device("gpio-keys.0", self.node)
        self.trial.find_gpio_keys(self.tree)
        deferred = self.tree / "deferred"
        deferred.write_text("gpio-keys.0\tGPIO supplier not ready\n6800000.remoteproc\tunrelated\n")
        output = StringIO()
        error = BlockingIOError(errno.EAGAIN, "deferred probe")
        with (
            patch.object(pogo, "DEFERRED_DEVICES", deferred),
            patch.object(pogo, "kernel_logs", return_value="gpio-keys.0: probe deferred"),
            patch.object(Path, "write_text", side_effect=error) as write,
            patch.object(pogo.time, "sleep"),
            redirect_stdout(output),
            self.assertRaisesRegex(RuntimeError, "内核暂缓探测"),
        ):
            self.trial.set_gpio_bound(True)
        self.assertEqual(write.call_count, 4)
        self.assertIn("GPIO supplier not ready", output.getvalue())
        self.assertNotIn("6800000.remoteproc", output.getvalue())

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
                    patch.object(started, "firmware", firmware),
                    patch.object(pogo, "report_module_failure"),
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
                if original:
                    self.assertEqual([action for action in actions if len(action) == 1],
                                     [("unbind",), ("bind",), ("unbind",), ("bind",)])
                else:
                    self.assertEqual(actions, [("insmod", "tb_pogo_overlay"),
                                               ("insmod", "lenovo_keyboard"),
                                               ("rmmod", "tb_pogo_overlay")])


class OverlayTests(unittest.TestCase):
    def assert_symbol_free_overlay(self, blob):
        self.assertNotIn(b"__symbols__\0", blob)
        self.assertNotIn(b"__fixups__\0", blob)
        self.assertIn(b"__local_fixups__\0", blob)

    def test_bundled_firmware_works_without_a_base_symbol_table(self):
        self.assert_symbol_free_overlay((ATTACHMENTS / "pogo-keyboard.dtbo").read_bytes())

    def test_uart_only_firmware_does_not_modify_gpio_keys(self):
        source = (ATTACHMENTS / "pogo-uart-only.dts").read_text()
        binary = pogo.Trial.prepared_dtb(source)
        self.assertEqual(binary, ATTACHMENTS / "pogo-uart-only.dtbo")
        blob = binary.read_bytes()
        self.assert_symbol_free_overlay(blob)
        self.assertNotIn(b"/gpio-keys\0", blob)
        self.assertNotIn(b"pogo-hall-default-state\0", blob)
        self.assertIn(b"serial@a9c000\0", blob)

    @unittest.skipUnless(shutil.which("dtc"), "device-tree-compiler is required")
    def test_runtime_compilation_keeps_only_local_fixups(self):
        dts = (ATTACHMENTS / "pogo-keyboard.dts").read_text()
        with patch.object(pogo.Trial, "prepared_dtb", return_value=None):
            self.assert_symbol_free_overlay(pogo.Trial().build_firmware(dts))

    def test_load_failure_reports_kernel_reason_and_propagates_error(self):
        diagnostic = "OF: overlay: symbols in overlay, but not in live tree"
        journal = subprocess.CompletedProcess(["journalctl"], 0, stdout=diagnostic)
        failed = subprocess.CalledProcessError(1, ["insmod"])
        trial = pogo.Trial()
        output = StringIO()
        with (
            patch.object(pogo.time, "time", return_value=1234.5),
            patch.object(pogo.subprocess, "run", side_effect=[failed, journal]) as run,
            redirect_stdout(output),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            trial.load("tb_pogo_overlay")
        self.assertEqual(trial.loaded, [])
        journal_args = run.call_args.args[0]
        self.assertIn("@1234", journal_args)
        self.assertIn("OF: (overlay|resolver)", journal_args[journal_args.index("--grep") + 1])
        self.assertIn(diagnostic, output.getvalue())


class PowerSupplyTests(unittest.TestCase):
    def test_accepts_discrete_3v3_selector_and_rejects_1v8_only_rail(self):
        pmic = Path(self.enterContext(TemporaryDirectory()))
        (pmic / "qcom,pmic-id").write_bytes(b"b\0")
        for node, name, maximum in (("ldo1", "vreg_l1b_1p8", 1800000), ("ldo8", "vreg_l8b_1p8", 3296000)):
            rail = pmic / node
            rail.mkdir()
            (rail / "regulator-name").write_bytes(name.encode() + b"\0")
            (rail / "regulator-min-microvolt").write_bytes((1800000).to_bytes(4, "big"))
            (rail / "regulator-max-microvolt").write_bytes(maximum.to_bytes(4, "big"))
        pogo.check_supplies(pmic)
        (pmic / "ldo8/regulator-max-microvolt").write_bytes((1800000).to_bytes(4, "big"))
        with self.assertRaisesRegex(RuntimeError, "ldo8"):
            pogo.check_supplies(pmic)


class GnomeShortcutTests(unittest.TestCase):
    fullscreen = "/org/gnome/desktop/wm/keybindings/toggle-fullscreen"
    tiled = "/org/gnome/mutter/keybindings/toggle-tiled-left"

    def setUp(self):
        run = Path(self.enterContext(TemporaryDirectory()))
        self.shortcuts = GnomeShortcuts.__new__(GnomeShortcuts)
        self.shortcuts.state = run / "state" / "shortcuts.json"
        self.defaults = {self.fullscreen: "@as []", self.tiled: "['<Super>Left']"}
        self.values = {self.fullscreen: "['<Alt>F12']"}
        self.enterContext(patch.object(self.shortcuts, "run", side_effect=self.command))

    def command(self, command, action, key, *args):
        if command == "gsettings":
            path = "/" + key.replace(".", "/") + "/" + args[0]
            return "true" if action == "writable" else self.values.get(path, self.defaults[path])
        if action == "read":
            return self.values.get(key, "")
        if action == "write":
            self.values[key] = args[0]
        else:
            self.values.pop(key, None)

    def test_preserves_existing_bindings_and_restores_unset_defaults(self):
        self.shortcuts.apply()
        self.assertEqual(self.values[self.fullscreen], "['<Alt>F12', 'XF86FullScreen']")
        self.assertEqual(self.values[self.tiled], "['<Super>Left', 'XF86AspectRatio']")
        self.assertTrue(self.shortcuts.state.is_file())
        self.shortcuts.restore()
        self.assertEqual(self.values, {self.fullscreen: "['<Alt>F12']"})
        self.assertFalse(self.shortcuts.state.exists())

    def test_recovery_keeps_unrelated_edits_and_original_shortcut(self):
        self.values[self.tiled] = "['XF86AspectRatio']"
        self.shortcuts.apply()
        self.values[self.fullscreen] = "['XF86FullScreen', '<Alt>F11']"
        self.shortcuts.restore()
        self.assertEqual(self.values, {self.fullscreen: "['<Alt>F11']", self.tiled: "['XF86AspectRatio']"})

    def test_interrupted_apply_can_be_recovered(self):
        def interrupted(command, action, key, *args):
            if command == "dconf" and action == "write" and key == self.tiled:
                raise subprocess.CalledProcessError(1, ["dconf"])
            return self.command(command, action, key, *args)

        with patch.object(self.shortcuts, "run", side_effect=interrupted):
            with self.assertRaises(subprocess.CalledProcessError):
                self.shortcuts.apply()
        self.assertEqual(self.values[self.fullscreen], "['<Alt>F12', 'XF86FullScreen']")
        self.shortcuts.restore()
        self.assertEqual(self.values, {self.fullscreen: "['<Alt>F12']"})


class InstallationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.prefix = root / "lib" / "pogo"
        self.unit = root / install.UNIT
        self.firmware = root / "firmware" / "pogo-keyboard.dtbo"
        self.dtbo = root / "run" / "pogo-keyboard.dtbo"
        self.assets = root / "assets"
        for name in install.PAYLOAD:
            path = self.assets / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"original {name}")
        self.active = self.enabled = self.fail_start = False
        self.enterContext(patch.multiple(install, PREFIX=self.prefix, UNIT_PATH=self.unit, DTBO=self.dtbo))
        self.enterContext(patch.multiple(pogo, ROOT=self.assets, FIRMWARE=self.firmware))
        self.enterContext(patch.object(install, "check_install"))
        self.enterContext(patch.object(install, "reload_units"))
        self.enterContext(patch.object(install, "setup_shortcuts"))
        self.enterContext(patch.object(install, "restore_shortcuts"))
        self.enterContext(patch.object(install, "systemctl", side_effect=self.systemctl))
        self.enterContext(redirect_stdout(StringIO()))

    def systemctl(self, action, check=True):
        if action == "start":
            self.assertTrue(self.unit.is_file())
            self.assertEqual(self.firmware.readlink(), self.dtbo)
            if self.fail_start:
                self.fail_start = False
                raise subprocess.CalledProcessError(1, ["systemctl", "start"])
            self.active = True
        if action == "stop":
            self.active = False
        if action in ("enable", "disable"):
            self.enabled = action == "enable"
        return self.active if action == "is-active" else True

    def test_install_is_immediate_idempotent_and_reversible(self):
        install.apply()
        self.assertTrue(self.active and self.enabled)
        self.assertTrue(install.installed_unit())
        self.assertEqual(self.firmware.readlink(), self.dtbo)
        self.assertFalse(self.dtbo.exists())  # Service generates it only in /run.
        install.apply()
        install.rollback()
        self.assertFalse(self.active or self.enabled)
        self.assertFalse(self.unit.exists() or self.prefix.exists() or self.firmware.is_symlink())

    def test_failed_first_start_leaves_no_autoload_or_files(self):
        self.fail_start = True
        with self.assertRaises(subprocess.CalledProcessError):
            install.apply()
        self.assertFalse(self.active or self.enabled)
        self.assertFalse(self.unit.exists() or self.prefix.exists() or self.firmware.is_symlink())

    def test_failed_upgrade_restores_previous_running_service(self):
        install.apply()
        (self.assets / install.UNIT).write_text("new service")
        self.fail_start = True
        with self.assertRaises(subprocess.CalledProcessError):
            install.apply()
        self.assertEqual(self.unit.read_text(), f"original {install.UNIT}")
        self.assertTrue(self.active and self.enabled and install.installed_unit())

    def test_preserves_a_firmware_file_owned_by_another_installer(self):
        self.firmware.parent.mkdir()
        self.firmware.write_text("unmanaged")
        with self.assertRaisesRegex(RuntimeError, "其他程序"):
            install.apply()
        self.assertEqual(self.firmware.read_text(), "unmanaged")
        self.assertFalse(self.unit.exists())

    def test_service_signals_ready_after_uart_start_and_cleans_up_on_stop(self):
        self.firmware.parent.mkdir()
        self.firmware.symlink_to(self.dtbo)
        order = []
        with (
            patch.object(install, "lock", return_value=nullcontext()),
            patch.object(install.signal, "signal"),
            patch.object(install.signal, "pause", side_effect=SystemExit(0)),
            patch.object(pogo, "Trial") as create,
            patch.object(install, "notify_ready", side_effect=lambda: order.append("ready")),
            self.assertRaises(SystemExit),
        ):
            trial = create.return_value
            trial.start.side_effect = lambda _: order.append("start")
            trial.stop.side_effect = lambda: order.append("stop")
            install.serve()
        self.assertEqual(order, ["start", "ready", "stop"])


if __name__ == "__main__":
    unittest.main()
