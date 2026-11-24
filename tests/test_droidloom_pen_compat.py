import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import tempfile

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "contributions/droidloom-pen-compat/attachments/pen-compat.py"
)


class PenCompatibilityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.destination = self.root / ".local/libexec/tb710fu-droidloom-pen-compat"
        self.dropin = self.root / (
            ".config/systemd/user/droidloom.service.d/90-tb710fu-pen-compat.conf"
        )
        spec = importlib.util.spec_from_file_location("pen_compat", SCRIPT)
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        (self.assets / self.installer.LAUNCHER).write_bytes(SCRIPT.read_bytes())
        (self.assets / self.installer.PRESENTER).write_bytes(b"test presenter")
        self.system_calls = []

    def systemctl(self, *args):
        self.system_calls.append(args)
        if args[0] == "show":
            argv = self.installer.STOCK
            if self.dropin.exists():
                argv = f"/usr/bin/python3 -IB {self.destination / self.installer.LAUNCHER} run"
            return f"{{ argv[]={argv} ; }}"
        return ""

    def invoke(self, action):
        with (
            patch.object(self.installer, "ASSETS", self.assets),
            patch.object(self.installer, "systemctl", self.systemctl),
            patch.object(self.installer, "verify_platform"),
            patch("pathlib.Path.home", return_value=self.root),
            patch("os.geteuid", return_value=1000),
            patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.root), XDG_CONFIG_HOME=""),
            patch("sys.argv", ["pen-compat.py", action]),
        ):
            self.installer.main()

    def test_apply_is_idempotent_and_rollback_removes_the_override(self):
        self.invoke("apply")
        self.assertEqual(self.dropin.read_bytes(), self.installer.OVERRIDE)
        presenter = self.destination / self.installer.PRESENTER
        self.assertEqual(presenter.read_bytes(), b"test presenter")
        self.assertTrue(os.access(presenter, os.X_OK))

        self.invoke("apply")
        self.assertEqual(self.system_calls.count(("daemon-reload",)), 1)
        self.invoke("rollback")
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.system_calls.count(("daemon-reload",)), 2)

    def test_existing_custom_override_is_preserved(self):
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_text("[Service]\nExecStart=/custom\n")
        with self.assertRaisesRegex(ValueError, "覆盖配置"):
            self.invoke("apply")
        self.assertEqual(self.dropin.read_text(), "[Service]\nExecStart=/custom\n")
        self.assertFalse(self.destination.exists())

    def test_failed_systemd_reload_leaves_original_service_in_place(self):
        normal_systemctl = self.systemctl

        def fail_reload(*args):
            if args[0] == "daemon-reload":
                raise subprocess.CalledProcessError(1, "systemctl")
            return normal_systemctl(*args)

        with patch.object(self, "systemctl", fail_reload):
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke("apply")
        self.assertFalse(self.dropin.exists())
        self.invoke("apply")
        self.assertTrue(self.dropin.exists())

    def test_next_droidloom_release_uses_original_presenter(self):
        self.invoke("apply")
        with (
            patch.object(self.installer, "compatible_package", return_value=False),
            patch.object(self.installer.os, "execv", side_effect=SystemExit) as execute,
            self.assertRaises(SystemExit),
        ):
            self.invoke("run")
        execute.assert_called_once_with(self.installer.STOCK, [str(self.installer.STOCK)])


if __name__ == "__main__":
    unittest.main()
