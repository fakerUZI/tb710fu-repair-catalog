import hashlib
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
ATTACHMENTS = SCRIPT.parent
PACKAGE_SCRIPT = ATTACHMENTS.parent / "script.sh"


class PackagedPresenterTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.assets = Path(temporary.name)
        for source in ATTACHMENTS.glob("droidloom-wayland*.gz.*"):
            (self.assets / source.name).symlink_to(source)
        (self.assets / "pen-compat.py").write_text(
            "from pathlib import Path\n"
            "import hashlib\n"
            "import sys\n"
            "path = Path(__file__).with_name('droidloom-wayland')\n"
            "print(sys.argv[1], hashlib.sha256(path.read_bytes()).hexdigest())\n"
        )

    def run_package(self, action):
        return subprocess.run(
            ["/bin/bash", str(PACKAGE_SCRIPT)],
            env=dict(os.environ, TB710FU_ATTACHMENTS_DIR=str(self.assets), TB710FU_FIX_ACTION=action),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_reassembles_the_original_presenter_before_running_the_installer(self):
        result = self.run_package("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "check 8202e635e20a72eee63402ca6b7c66729e5fb0033d97413fee8cf7a97816c994",
        )

    def test_corrupt_part_prevents_running_the_installer(self):
        part = self.assets / "droidloom-wayland.gz.003"
        part.unlink()
        part.write_bytes(b"broken")
        result = self.run_package("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("分片不完整或校验失败", result.stderr)


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
        (self.assets / self.installer.PRESENTER_R42).write_bytes(b"test r42 presenter")
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
            patch.object(self.installer, "compatible_presenter", return_value=None),
            patch.object(self.installer.os, "execv", side_effect=SystemExit) as execute,
            self.assertRaises(SystemExit),
        ):
            self.invoke("run")
        execute.assert_called_once_with(self.installer.STOCK, [str(self.installer.STOCK)])

    def test_release_selects_matching_android_input_abi(self):
        library = self.root / "libinputflinger.so"
        for release, expected, abi in [
            (42, self.installer.PRESENTER_R42, 7),
            (43, self.installer.PRESENTER, 8),
            (48, self.installer.PRESENTER, 8),
            (49, self.installer.PRESENTER, 8),
            (41, None, 7),
            (50, None, 8),
        ]:
            with self.subTest(release=release):
                package = {"version": "0.1.0", "architecture": "aarch64", "release": release}
                with patch.object(self.installer, "INPUT_FLINGER", library):
                    library.write_bytes(f"DROIDLOOM_INPUT_ABI={abi:05d};DROIDLOOM_NATIVE_INPUT_ABI=2;".encode())
                    self.assertEqual(self.installer.compatible_presenter(package), expected)
                    library.write_bytes(b"DROIDLOOM_INPUT_ABI=00099;DROIDLOOM_NATIVE_INPUT_ABI=2;")
                    self.assertIsNone(self.installer.compatible_presenter(package))

    def test_existing_test_installer_can_upgrade_in_place(self):
        legacy = b"previous release 49 launcher"
        self.destination.mkdir(parents=True)
        launcher = self.destination / self.installer.LAUNCHER
        launcher.write_bytes(legacy)
        (self.destination / self.installer.PRESENTER).write_bytes(b"test presenter")
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_bytes(self.installer.OVERRIDE)
        with patch.object(self.installer, "LEGACY_LAUNCHER_SHA", hashlib.sha256(legacy).hexdigest()):
            self.invoke("apply")
        self.assertEqual(launcher.read_bytes(), SCRIPT.read_bytes())
        self.assertEqual((self.destination / self.installer.PRESENTER_R42).read_bytes(), b"test r42 presenter")
        self.assertNotIn(("daemon-reload",), self.system_calls)


if __name__ == "__main__":
    unittest.main()
