import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, Mock, patch


SOURCE = (Path(__file__).resolve().parents[1] /
          "contributions/gnome-login-display/attachments/login-display.py")
SPEC = importlib.util.spec_from_file_location("gnome_login_display", SOURCE)
display = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(display)


def monitor_state(hz=144, *, external=False):
    spec = ("DSI-1", "unknown", "unknown", "unknown")
    mode = ("mode-id", 3200, 2000, hz, 2.0, [2.0], {"is-current": True})
    available = [(spec, [mode], {"is-builtin": True})]
    if external:
        available.append((("DP-1", "Monitor", "Display", "serial"), [], {}))
    return 4, available, [(0, 0, 2.0, 0, True, [spec], {})], {}


class LoginDisplayTests(unittest.TestCase):
    def setUp(self):
        self.bus = Mock()
        self.bus.locked.return_value = False
        self.bus.state.return_value = monitor_state()
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.stamp = root / "session"
        self.dpms = root / "dpms"
        self.dpms.write_text("On")

    def test_refresh_and_external_display_boundaries(self):
        self.assertEqual(display.panel_mode(monitor_state(143.9999), display.LOGIN_RATES), (4, "DSI-1", 144))
        self.assertEqual(display.panel_mode(monitor_state(59.9999), display.LOGIN_RATES), (4, "DSI-1", 60))
        self.assertIsNone(display.panel_mode(monitor_state(120), display.LOGIN_RATES))
        self.assertIsNone(display.panel_mode(monitor_state(external=True), display.LOGIN_RATES))

    def test_wake_even_if_panel_does_not_confirm_off(self):
        with patch.object(display, "wait_dpms", side_effect=[False, True]):
            with self.assertRaisesRegex(RuntimeError, "DPMS Off"):
                display.cycle(self.bus, self.dpms, (OSError,))
        self.assertEqual(self.bus.set_mode.call_args_list, [call(display.OFF), call(display.ON)])

    def test_retry_wake_after_transient_dbus_failure(self):
        self.bus.set_mode.side_effect = [None, OSError("D-Bus connection busy"), None]
        with patch.object(display, "wait_dpms", return_value=True), patch.object(display.time, "sleep"):
            display.cycle(self.bus, self.dpms, (OSError,))
        self.assertEqual(self.bus.set_mode.call_args_list,
                         [call(display.OFF), call(display.ON), call(display.ON)])

    def test_waits_for_desktop_and_recovers_only_once(self):
        self.bus.locked.side_effect = [True, False, False, False]
        with patch.object(display, "active_session", return_value="session-4"), \
                patch.object(display, "panel_status", return_value=self.dpms), \
                patch.object(display.time, "sleep"), \
                patch.object(display.time, "monotonic", side_effect=range(0, 55, 2)), \
                patch.object(display, "restore_panel") as restore:
            for _ in range(2):
                display.recover(self.bus, self.stamp, (OSError,))
        restore.assert_called_once_with(self.bus, self.dpms, 144, "login", (OSError,))
        self.assertEqual(self.stamp.read_text(), "session-4\n")

    def test_login_recovers_60_hz_even_when_drm_reports_off(self):
        self.bus.state.return_value = monitor_state(60)
        self.dpms.write_text("Off")
        with patch.object(display, "active_session", return_value="login-60"), \
                patch.object(display, "panel_status", return_value=self.dpms), \
                patch.object(display.time, "sleep"), \
                patch.object(display.time, "monotonic", side_effect=range(0, 55, 2)), \
                patch.object(display, "restore_panel") as restore:
            self.assertTrue(display.recover(self.bus, self.stamp, (OSError,)))
        restore.assert_called_once_with(self.bus, self.dpms, 60, "login", (OSError,))

    def unlock_recovery(self):
        glib = Mock(SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
        glib.timeout_add.return_value = 1
        return display.UnlockRecovery(self.bus, glib, (OSError,))

    def screen_saver_changed(self, recovery, locked):
        arguments = Mock()
        arguments.unpack.return_value = (locked,)
        recovery.changed(None, None, None, None, None, arguments)

    def test_unlock_recovers_once_at_60_hz(self):
        self.bus.state.return_value = monitor_state(60)
        recovery = self.unlock_recovery()
        for locked in (True, False, False):
            self.screen_saver_changed(recovery, locked)
        recovery.glib.timeout_add.assert_called_once()
        with patch.object(display, "active_session", return_value="sess"), \
                patch.object(display, "panel_status", return_value=self.dpms), \
                patch.object(display, "restore_panel") as restore:
            self.assertFalse(recovery.after_unlock())
        restore.assert_called_once_with(self.bus, self.dpms, 60, "unlock", (OSError,))

    def test_unlock_waits_for_logind_and_cancels_if_locked_again(self):
        recovery = self.unlock_recovery()
        self.screen_saver_changed(recovery, True)
        self.screen_saver_changed(recovery, False)
        with patch.object(display, "active_session", return_value=None):
            self.assertTrue(recovery.after_unlock())
        self.screen_saver_changed(recovery, True)
        recovery.glib.source_remove.assert_called_once_with(1)

    def test_unlock_skips_other_modes_even_when_screen_is_dark(self):
        recovery = self.unlock_recovery()
        self.dpms.write_text("Off")
        self.screen_saver_changed(recovery, True)
        self.screen_saver_changed(recovery, False)
        with patch.object(display, "active_session", return_value="sess"), \
                patch.object(display, "panel_status", return_value=self.dpms), \
                patch.object(display, "restore_panel") as restore:
            self.assertFalse(recovery.after_unlock())
        restore.assert_not_called()

    def test_dual_screen_does_not_cycle(self):
        self.bus.state.return_value = monitor_state(external=True)
        with patch.object(display, "active_session", return_value="session-8"), \
                patch.object(display.time, "sleep"), \
                patch.object(display.time, "monotonic", side_effect=range(0, 55, 2)), \
                patch.object(display, "cycle") as cycle:
            display.recover(self.bus, self.stamp, (OSError,))
        cycle.assert_not_called()
        self.assertFalse(self.stamp.exists())


if __name__ == "__main__":
    unittest.main()
