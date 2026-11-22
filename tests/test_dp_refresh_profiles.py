import copy
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parents[1] /
          "contributions/dp-refresh-profiles/attachments/display-profiles.py")
spec = importlib.util.spec_from_file_location("display_profiles", SCRIPT)
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)

DSI = ("DSI-1", "test-vendor", "test-panel", "")
DP = ("DP-1", "test-vendor", "test-monitor", "synthetic-only")


def xml_profile(entries):
    lines = ["<configuration>"]
    for identity, width, height, rate in entries:
        lines.extend(("<logicalmonitor><monitor><monitorspec>",
                      "".join(f"<{key}>{value}</{key}>" for key, value in
                              zip(("connector", "vendor", "product", "serial"), identity)),
                      f"</monitorspec><mode><width>{width}</width><height>{height}</height>"
                      f"<rate>{rate}</rate></mode></monitor></logicalmonitor>"))
    return "".join(lines) + "</configuration>"


def document(*entries):
    return ('<monitors version="2">' + "".join(xml_profile(entry) for entry in entries) +
            "</monitors>").encode()


def screen_state(dsi=144.0, dp=165.0):
    def mode(width, height, rate, current):
        return (f"{width}x{height}@{rate:.3f}", width, height, rate, 1.0, [1.0, 2.0],
                {"is-current": rate == current})
    monitors = [
        (DSI, [mode(3200, 2000, rate, dsi) for rate in (120.0, 144.0)], {}),
        (DP, [mode(2560, 1440, rate, dp) for rate in (59.951, 165.0)],
         {"color-mode": 0, "is-underscanning": False}),
    ]
    logical = [(0, 0, 2.0, 0, True, [DSI], {}),
               (1600, 0, 1.0, 0, False, [DP], {})]
    return 7, monitors, logical, {"layout-mode": 1}


class FakeMutter:
    def __init__(self, state, config=None, saved=None):
        self.current = state
        self.config = config
        self.saved = saved
        self.calls = []

    def state(self):
        return self.current

    def apply(self, serial, method, layout, layout_mode):
        self.calls.append((serial, method, layout, layout_mode))
        if method in (1, 2):
            self.current = screen_state(dsi=120.0, dp=59.951)
        if method == 2 and self.config is not None and self.saved is not None:
            self.config.write_bytes(self.saved)


class DisplayProfiles(unittest.TestCase):
    single = [(DSI, 3200, 2000, 144.0)]
    dual = [(DSI, 3200, 2000, 120.0), (DP, 2560, 1440, 59.951)]

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / "monitors.xml"
        self.receipt = Path(temp.name) / "state" / "rollback.json"
        self.original = document(self.single)
        self.applied = document(self.dual, self.single)
        self.config.write_bytes(self.original)
        self.stdout = io.StringIO()
        redirect = redirect_stdout(self.stdout)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def test_plan_preserves_layout_and_single_refresh(self):
        state = screen_state()
        _, layout, _, modes, original, selected = profiles.build_plan(
            state, profiles.parse_profiles(document([(DSI, 3200, 2000, 60.0)]))
        )
        self.assertEqual(original, (3200, 2000, 60.0))
        self.assertEqual(modes[DSI], (3200, 2000, 120.0))
        self.assertEqual(modes[DP], (2560, 1440, 59.951))
        self.assertEqual(selected["DP-1"][0], "2560x1440@59.951")
        self.assertEqual([row[:5] for row in layout], [row[:5] for row in state[2]])
        self.assertEqual(layout[1][5][0][2], {"color-mode": ("u", 0), "underscanning": ("b", False)})

    def test_apply_and_rollback_preserve_exact_original(self):
        mutter = FakeMutter(screen_state(), self.config, self.applied)
        profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertEqual([call[1] for call in mutter.calls], [0, 2])
        self.assertEqual(self.config.read_bytes(), self.applied)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(DP[-1], self.stdout.getvalue())
        profiles.rollback(self.config, self.receipt)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.receipt.exists())

    def test_already_correct_is_a_no_op(self):
        self.config.write_bytes(self.applied)
        mutter = FakeMutter(screen_state(120.0, 59.951))
        profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertFalse(mutter.calls)
        self.assertFalse(self.receipt.parent.exists())

    def test_saved_config_can_be_applied_temporarily(self):
        self.config.write_bytes(self.applied)
        mutter = FakeMutter(screen_state())
        profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertEqual([call[1] for call in mutter.calls], [0, 1])
        self.assertFalse(self.receipt.parent.exists())

    def test_unconfirmed_save_fails_without_applied_receipt(self):
        mutter = FakeMutter(screen_state())
        with patch.object(profiles.time, "monotonic", side_effect=[0, 33]):
            with self.assertRaisesRegex(RuntimeError, "确认窗口"):
                profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_verify_failure_does_not_write(self):
        mutter = FakeMutter(screen_state())
        with patch.object(mutter, "apply", side_effect=RuntimeError("rejected")):
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertFalse(self.receipt.parent.exists())

    def test_unknown_save_result_keeps_the_private_backup(self):
        mutter = FakeMutter(screen_state())
        with patch.object(mutter, "apply", side_effect=[None, TimeoutError()]):
            with self.assertRaises(TimeoutError):
                profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertTrue(self.receipt.exists())
        self.assertEqual(self.config.read_bytes(), self.original)
        with self.assertRaisesRegex(RuntimeError, "上次写入未完成"):
            profiles.rollback(self.config, self.receipt)

    def test_simultaneous_single_profile_change_blocks_rollback_receipt(self):
        changed = document([(DSI, 3200, 2000, 60.0)], self.dual)
        mutter = FakeMutter(screen_state(), self.config, changed)
        with self.assertRaisesRegex(RuntimeError, "同时发生变化"):
            profiles.apply_profile(mutter, self.config, self.receipt)
        self.assertTrue(self.receipt.exists())
        self.assertEqual(self.config.read_bytes(), changed)
        with self.assertRaisesRegex(RuntimeError, "上次写入未完成"):
            profiles.rollback(self.config, self.receipt)

    def test_rollback_refuses_to_overwrite_later_changes(self):
        mutter = FakeMutter(screen_state(), self.config, self.applied)
        profiles.apply_profile(mutter, self.config, self.receipt)
        changed = document([(DSI, 3200, 2000, 60.0)], self.dual)
        self.config.write_bytes(changed)
        with self.assertRaisesRegex(RuntimeError, "后来有变"):
            profiles.rollback(self.config, self.receipt)
        self.assertEqual(self.config.read_bytes(), changed)
        self.assertTrue(self.receipt.exists())

    def test_refuses_missing_single_profile(self):
        with self.assertRaisesRegex(RuntimeError, "单屏刷新率"):
            profiles.build_plan(screen_state(), {})

    def test_missing_60hz_and_mirror_fail_before_apply(self):
        state = copy.deepcopy(screen_state())
        state[1][1][1].pop(0)
        with self.assertRaisesRegex(RuntimeError, "缺少可用"):
            profiles.build_plan(state, profiles.parse_profiles(self.original))
        state = screen_state()
        state[2][:] = [(0, 0, 1.0, 0, True, [DSI, DP], {})]
        with self.assertRaisesRegex(RuntimeError, "加入显示"):
            profiles.build_plan(state, profiles.parse_profiles(self.original))

    def test_single_screen_read_only_check(self):
        self.config.write_bytes(self.applied)
        state = screen_state()
        state[1].pop()
        state[2].pop()
        mutter = FakeMutter(state)
        profiles.check_profile(mutter, self.config)
        self.assertIn("单屏保存 144.000Hz", self.stdout.getvalue())
        self.assertIn("已有 1 套", self.stdout.getvalue())
        self.assertEqual(mutter.calls, [])
        self.assertEqual(self.config.read_bytes(), self.applied)

    def test_saved_pair_must_match_current_display_identity(self):
        state = screen_state()
        changed_id = ("DP-1", "test-vendor", "test-monitor", "different-synthetic")
        saved = document(self.single, [self.dual[0], (changed_id, 2560, 1440, 59.951)])
        parsed = profiles.parse_profiles(saved)
        _, _, _, wanted, _, _ = profiles.build_plan(state, parsed)
        self.assertFalse(profiles.agrees(parsed, wanted))


if __name__ == "__main__":
    unittest.main()
