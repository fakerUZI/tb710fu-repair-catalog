import ast
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

entry = Path(__file__).resolve().parents[1]/'contributions/torch-quick-settings'
spec = importlib.util.spec_from_file_location('torch_quick_settings', entry/'attachments/repair.py')
torch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(torch)


class TorchQuickSettingsTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.directory = Path(workspace.name)/'extension'
        self.directory.mkdir(mode=0o700)
        self.record = Path(workspace.name)/'record.json'
        self.original = {torch.KEYS[0]: ['keep@local'],
                         torch.KEYS[1]: [torch.UUID, 'other@local']}
        self.selection = {key: list(value) for key, value in self.original.items()}

        def settings(uid, action, key, value):
            self.assertEqual(uid, 1000)
            self.assertEqual(action, 'set')
            self.selection[key] = ast.literal_eval(value)

        patches = patch.multiple(torch, snapshot_path=lambda uid: self.record,
                                 settings_command=settings)
        patches.start()
        self.addCleanup(patches.stop)

    def test_apply_twice_and_rollback_only_torch_selection(self):
        torch.apply(1000, self.original, self.directory)
        contents = self.record.read_bytes()
        torch.apply(1000, self.selection, self.directory)
        self.assertEqual(self.record.read_bytes(), contents)
        self.assertEqual(self.selection[torch.KEYS[0]], ['keep@local', torch.UUID])
        self.assertEqual(self.selection[torch.KEYS[1]], ['other@local'])
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o755)
        self.selection[torch.KEYS[0]].append('added@local')
        torch.rollback(1000, self.selection)
        self.assertEqual(self.selection[torch.KEYS[0]], ['keep@local', 'added@local'])
        self.assertEqual(self.selection[torch.KEYS[1]], self.original[torch.KEYS[1]])
        self.assertFalse(self.record.exists())
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o755)

    def test_failed_settings_write_restores_prior_changes(self):
        settings = torch.settings_command

        def fail_second(uid, action, key, value):
            if key == torch.KEYS[1]:
                raise subprocess.CalledProcessError(1, 'gsettings')
            return settings(uid, action, key, value)

        with patch.object(torch, 'settings_command', side_effect=fail_second):
            with self.assertRaises(subprocess.CalledProcessError):
                torch.apply(1000, self.original, self.directory)
        self.assertEqual(self.selection, self.original)
        self.assertTrue(self.record.exists())


if __name__ == '__main__':
    unittest.main()
