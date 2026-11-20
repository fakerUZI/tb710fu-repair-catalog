import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_repair(kind):
    source = ROOT / "contributions" / f"droidloom-window-{kind}" / "attachments" / "install.py"
    spec = importlib.util.spec_from_file_location(kind, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


navigation = load_repair("navigation")
memory = load_repair("memory")
LEGACY = "tb-android-window-defaults@tb710fu"
KEYBOARD = "tb-osk-shortcuts@tb710fu"


class WindowRepairTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.state = self.root / "before.json"

    def test_apply_and_rollback_restore_only_android_selection(self):
        extension = self.root / "extensions" / navigation.UUID
        legacy_dir = extension.with_name(LEGACY)
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "extension.js").write_text("old extension")
        current = {"enabled-extensions": ["unrelated@host", LEGACY, KEYBOARD],
                   "disabled-extensions": [navigation.UUID, "another@host"]}
        payload = navigation.read_payload()
        with patch.multiple(navigation, settings=current.__getitem__, set_settings=current.__setitem__):
            navigation.apply(self.state, extension, payload)
            navigation.apply(self.state, extension, payload)
            self.assertTrue(navigation.matching(extension, payload))
            self.assertEqual(current["enabled-extensions"], ["unrelated@host", navigation.UUID, KEYBOARD])
            self.assertEqual(current["disabled-extensions"], ["another@host"])
            current["enabled-extensions"].append("added@host")
            navigation.rollback(self.state, extension, payload)
        self.assertEqual(current["enabled-extensions"], ["unrelated@host", LEGACY, KEYBOARD, "added@host"])
        self.assertEqual(current["disabled-extensions"], [navigation.UUID, "another@host"])
        self.assertEqual((legacy_dir / "extension.js").read_text(), "old extension")
        self.assertFalse(extension.exists())
        self.assertFalse(self.state.exists())

    def test_navigation_preserves_identical_prior_files_and_rejects_drift(self):
        extension = self.root / "extensions" / navigation.UUID
        payload = navigation.read_payload()
        navigation.install_files(extension, payload)
        current = {"enabled-extensions": [], "disabled-extensions": []}
        with patch.multiple(navigation, settings=current.__getitem__, set_settings=current.__setitem__):
            navigation.apply(self.state, extension, payload)
            navigation.rollback(self.state, extension, payload)
            self.assertTrue(navigation.matching(extension, payload))
            (extension / "stylesheet.css").write_bytes(b"custom content")
            with self.assertRaisesRegex(ValueError, "不同内容"):
                navigation.apply(self.state, extension, payload)
        self.assertFalse(self.state.exists())
        self.assertEqual((extension / "stylesheet.css").read_bytes(), b"custom content")

    def test_memory_rollback_leaves_existing_config_and_removes_added_package(self):
        configs = {key: self.root / target.name for key, target in memory.CONFIGS.items()}
        payload = memory.payload()
        configs["watermark"].write_bytes(payload["watermark"])
        packages = set()

        def invoke(*args, check=True):
            if args[0] == "env":
                packages.add(memory.GENERATOR)
            elif args == ("dpkg", "--remove", memory.GENERATOR):
                packages.remove(memory.GENERATOR)
            else:
                self.fail(f"Unexpected command: {args}")
            return subprocess.CompletedProcess(args, 0)

        with patch.multiple(memory, CONFIGS=configs, installed=packages.__contains__, run=invoke):
            memory.apply(self.state, payload)
            memory.apply(self.state, payload)
            self.assertEqual(configs["zram"].read_bytes(), payload["zram"])
            self.assertTrue(packages)
            memory.rollback(self.state, payload)
            self.assertFalse(configs["zram"].exists())
            self.assertFalse(packages)
            self.assertFalse(self.state.exists())
            self.assertEqual(configs["watermark"].read_bytes(), payload["watermark"])

    def test_memory_failed_package_install_undoes_its_new_config(self):
        configs = {key: self.root / target.name for key, target in memory.CONFIGS.items()}
        payload = memory.payload()
        error = subprocess.CalledProcessError(101, "apt-get")
        with patch.object(memory, "CONFIGS", configs), patch.object(
            memory, "installed", return_value=False
        ), patch.object(memory, "run", side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                memory.apply(self.state, payload)
        self.assertFalse(self.state.exists())
        self.assertFalse(any(path.exists() for path in configs.values()))

    def test_memory_existing_extra_provider_is_preserved(self):
        provider = self.root / "zram-generator.conf"
        provider.write_text("[zram0]\nzram-size = 2000\n")
        with patch.multiple(memory, SEARCH=(self.root,), installed=lambda _name: False):
            with self.assertRaisesRegex(ValueError, "已有 zram0"):
                memory.check_provider()
        self.assertTrue(provider.is_file())
