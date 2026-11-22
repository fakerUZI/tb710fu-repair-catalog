import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = (Path(__file__).resolve().parents[1] /
          "contributions/flash-portable-runtime/attachments/torch-quick-settings.py")
SPEC = importlib.util.spec_from_file_location("torch_quick_settings", SCRIPT)
torch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(torch)


class TorchQuickSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = self.folder / torch.UUID
        self.enterContext(patch.object(torch, "EXTENSIONS", self.folder))
        self.command = ("/usr/sbin/runuser", "-u", "tablet-user")

    def test_recovers_missing_extension_and_keeps_existing_code(self):
        self.assertTrue(torch.install_extension("50"))
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o755)
        module = self.target / "extension.js"
        packaged = (SCRIPT.parent / "torch-extension.js").read_bytes()
        self.assertEqual(module.read_bytes(), packaged)
        module.unlink()
        self.assertTrue(torch.install_extension("50"))
        self.assertEqual(module.read_bytes(), packaged)
        module.write_bytes(packaged + b"\n// local change\n")
        self.assertFalse(torch.install_extension("50"))
        self.assertTrue(module.read_bytes().endswith(b"// local change\n"))

    def test_recovers_root_only_permissions_from_existing_images(self):
        torch.install_extension("50")
        self.target.chmod(0o700)
        for name in torch.FILES:
            (self.target / name).chmod(0o600)
        self.assertTrue(torch.install_extension("50"))
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o755)
        for name in torch.FILES:
            self.assertEqual((self.target / name).stat().st_mode & 0o777, 0o644)

    def test_refuses_to_install_incompatible_extension(self):
        with self.assertRaisesRegex(RuntimeError, "GNOME Shell 49"):
            torch.install_extension("49")
        self.assertFalse(self.target.exists())

    def test_registered_extension_uses_gnome_to_enable(self):
        def respond(*args):
            output = "\n  State: ACTIVE\n" if args[-2] == "info" else ""
            return subprocess.CompletedProcess(args, 0, output, "")

        with patch.object(torch, "run", side_effect=respond) as invoke:
            torch.enable(self.command, installed=False)
        self.assertEqual([call.args[-2] for call in invoke.call_args_list],
                         ["info", "enable", "info"])

    def test_extension_not_yet_visible_queues_only_this_toggle(self):
        values = {"enabled-extensions": "['dock@example', 'foo@example']",
                  "disabled-extensions": f"['other@example', '{torch.UUID}']"}
        writes = {}

        def settings(_command, action, key, *args):
            if action == "get":
                return values[key]
            if action == "writable":
                return "true"
            writes[key] = json.loads(args[0])
            return ""

        missing = subprocess.CompletedProcess([], 1, "", "not found")
        with patch.object(torch, "run", return_value=missing), patch.object(torch, "setting", settings):
            torch.enable(self.command, installed=True)
        self.assertEqual(writes, {"enabled-extensions": ["dock@example", "foo@example", torch.UUID],
                                  "disabled-extensions": ["other@example"]})


if __name__ == "__main__":
    unittest.main()
