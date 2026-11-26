from contextlib import nullcontext
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SNIPPET = Path(__file__).resolve().parents[1] / 'contributions/bootfix-clock-sync/attachments/install.py'
SPEC = importlib.util.spec_from_file_location('bootfix_clock_sync', SNIPPET)
CLOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLOCK)


class BootfixClockSync(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(CLOCK, 'STATE_DIR', root / 'state'))
        self.enterContext(patch.object(CLOCK, 'DROP_IN', root / 'etc/timesyncd.conf.d/60-tb710fu-clock-sync.conf'))
        self.loaded = self.enterContext(patch.object(CLOCK, 'loaded', return_value=True))
        self.run = self.enterContext(patch.object(CLOCK, 'run'))
        self.installed = self.enterContext(patch.object(CLOCK, 'installed_version', return_value=None))
        self.unit_state = self.enterContext(patch.object(CLOCK, 'unit_state'))
        self.unit_state.side_effect = lambda unit: {'enabled': unit == CLOCK.OLD_TIMER, 'active': unit == CLOCK.OLD_TIMER}

    def calls(self):
        return [item.args for item in self.run.call_args_list]

    def test_check_does_not_create_state_or_change_services(self):
        self.run.return_value = 'NTP=no\nNTPSynchronized=no'
        CLOCK.operate('check')
        self.assertFalse(CLOCK.STATE_DIR.exists())
        self.assertFalse(CLOCK.DROP_IN.exists())
        self.assertEqual(self.calls(), [('timedatectl', 'show', '-p', 'NTP', '-p', 'NTPSynchronized')])

    def test_original_offline_package_is_pinned_and_used(self):
        self.installed.return_value = CLOCK.STOCK_VERSION
        with CLOCK.package_source() as name:
            self.assertTrue(Path(name).is_file())
            self.assertEqual(Path(name).name, CLOCK.STOCK_FILE)
        self.assertFalse(Path(name).exists())

    def test_other_systemd_version_uses_matching_ubuntu_package(self):
        self.installed.return_value = '259.9-0ubuntu0.1'
        with CLOCK.package_source() as name:
            self.assertEqual(name, 'systemd-timesyncd')

    def test_apt_can_resolve_dependencies_without_removing_packages(self):
        CLOCK.install_timesyncd('systemd-timesyncd')
        args, = self.calls()
        self.assertIn('--no-remove', args)
        self.assertNotIn('-s', args)

    def test_absent_legacy_timer_does_not_block_install(self):
        self.loaded.return_value = False
        CLOCK.enable_native(None)
        self.assertFalse(any(CLOCK.OLD_TIMER in cmd for cmd in self.calls()))
        self.assertIn(('systemctl', 'restart', CLOCK.SERVICE), self.calls())

    def test_apply_uses_distro_package_then_starts_native_sync(self):
        self.run.return_value = 'Inst systemd-timesyncd (matched)\nConf systemd-timesyncd (matched)'
        with patch.object(CLOCK, 'package_source', return_value=nullcontext('/tmp/matching.deb')):
            CLOCK.apply(None)
        self.assertEqual(CLOCK.DROP_IN.read_bytes(), CLOCK.CONFIG)
        self.assertTrue(CLOCK.load_state()['legacy']['active'])
        calls = self.calls()
        self.assertIn(('systemctl', 'disable', '--now', CLOCK.OLD_TIMER), calls)
        self.assertIn(('systemctl', 'restart', CLOCK.SERVICE), calls)
        self.assertEqual(sum(cmd[0] == 'apt-get' for cmd in calls), 1)

    def test_restart_failure_restores_original_services(self):
        self.installed.return_value = CLOCK.STOCK_VERSION
        def fail_restart(*args, **kwargs):
            if args == ('systemctl', 'restart', CLOCK.SERVICE):
                raise RuntimeError('simulated failure')
            return ''
        self.run.side_effect = fail_restart
        with patch.object(CLOCK, 'call') as loaded:
            loaded.return_value.stdout = 'loaded\n'
            with self.assertRaises(RuntimeError):
                CLOCK.apply(None)
        self.assertFalse(CLOCK.DROP_IN.exists())
        self.assertIsNone(CLOCK.load_state())
        self.assertIn(('systemctl', 'disable', CLOCK.SERVICE), self.calls())
        self.assertIn(('systemctl', 'start', CLOCK.OLD_TIMER), self.calls())

    def test_unmanaged_configuration_is_never_replaced(self):
        CLOCK.DROP_IN.parent.mkdir(parents=True)
        CLOCK.DROP_IN.write_bytes(b'[Time]\nNTP=local.example\n')
        with self.assertRaises(ValueError):
            CLOCK.operate('apply')
        self.assertEqual(CLOCK.DROP_IN.read_bytes(), b'[Time]\nNTP=local.example\n')
        self.run.assert_not_called()
