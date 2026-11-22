import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


INSTALLER = (Path(__file__).resolve().parents[1]
             / "contributions/screen-gestures/attachments/install.py")
SPEC = importlib.util.spec_from_file_location("screen_gestures", INSTALLER)
gestures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gestures)


class ModulePriorityTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backup = root / "backup"
        self.backup.mkdir()
        self.config = root / "depmod.conf"
        self.drivers = root / "drivers"
        self.drivers.mkdir()
        self.legacy = gestures.driver_override(legacy=True)
        self.config.write_bytes(self.legacy)
        self.original = b"search updates ubuntu built-in\n"
        (self.backup / "original-rule").write_bytes(self.original)
        self.receipt = self.backup / "state.json"
        state = {"drivers": True, "files": {str(self.config): {
            "sha256": gestures.sha(self.legacy), "original_sha256": gestures.sha(self.original),
            "original": "original-rule", "original_mode": 0o644,
        }}}
        for name in gestures.MODULE_FILES.values():
            path = self.drivers / name
            path.write_bytes(name.encode())
            state["files"][str(path)] = {"sha256": gestures.file_sha(path)}
        self.receipt.write_text(json.dumps(state))
        self.old_receipt = self.receipt.read_bytes()
        desktop = root / "desktop"
        desktop.write_bytes(b"installed gestures")
        self.enterContext(patch.multiple(
            gestures, BASE=self.backup, DEPMOD_CONF=self.config, DRIVERS=self.drivers,
            DING=desktop, PERMISSIONS=desktop, clean_units=lambda _state: None,
            verify_payload=lambda *_args: {}, gesture_edit=lambda *_args: (desktop.read_bytes(), 0o644),
        ))
        self.select_refresh = False
        self.run = self.enterContext(patch.object(gestures, "run", side_effect=self.command))

    def command(self, *args):
        if args[0] == "/usr/sbin/modinfo":
            module = args[-1]
            directory = self.drivers
            if module == gestures.PANEL and self.select_refresh:
                directory = self.drivers.with_name("refresh")
            return str(directory / gestures.MODULE_FILES[module])
        return ""

    def test_override_matches_ko_basename_and_only_target_kernel(self):
        rules = [line.split() for line in gestures.driver_override().decode().splitlines()]
        self.assertEqual({name for _, name, _, _ in rules},
                         {"nvt_36xxx", "panel-nt36532-wqhd-dual-dsi-dsc"})
        for command, _, version, directory in rules:
            self.assertEqual(command, "override")
            self.assertEqual(directory, gestures.DRIVER_SUBDIR)
            self.assertIsNotNone(re.search(version, gestures.KERNEL))
            self.assertIsNone(re.search(version, gestures.KERNEL + "-local"))

    def test_reapply_updates_legacy_rule_and_preserves_rollback(self):
        gestures.install(check=True)
        self.run.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.legacy)
        self.assertEqual(self.receipt.read_bytes(), self.old_receipt)

        gestures.install()
        self.assertEqual(self.config.read_bytes(), gestures.driver_override())
        self.assertIn(("/usr/sbin/depmod", "-a", gestures.KERNEL),
                      [call.args for call in self.run.call_args_list])
        self.run.reset_mock()
        gestures.install()
        self.run.assert_not_called()
        gestures.rollback()
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.drivers.exists())

    def test_reapply_restores_old_rule_when_module_selection_fails(self):
        self.select_refresh = True
        with self.assertRaisesRegex(RuntimeError, "选中了 .*refresh"):
            gestures.install()
        self.assertEqual(self.config.read_bytes(), self.legacy)
        self.assertEqual(self.receipt.read_bytes(), self.old_receipt)
        self.select_refresh = False
        gestures.install()
        self.assertEqual(self.config.read_bytes(), gestures.driver_override())

    def test_rollback_accepts_rule_written_before_receipt(self):
        self.config.write_bytes(gestures.driver_override())
        gestures.rollback()
        self.assertEqual(self.config.read_bytes(), self.original)
