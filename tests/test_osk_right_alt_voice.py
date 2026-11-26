import importlib.util
import json
from pathlib import Path
import tempfile
from unittest import TestCase, mock

FILE = Path(__file__).resolve().parents[1] / "contributions/osk-right-alt-voice/attachments/manage.py"
spec = importlib.util.spec_from_file_location("osk_voice", FILE)
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


class VoiceInstallTests(TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, location in (("TARGET", "user data/gnome-shell/extensions/" + voice.UUID),
                               ("STATE", "state"), ("CONFIG", "home elsewhere/settings/conf/wowtype.conf"),
                               ("MODEL", "shared model")):
            self.enterContext(mock.patch.object(voice, name, root / location))
        self.members = [False, True]
        self.enterContext(mock.patch.object(voice, "membership", lambda _: self.members[:]))
        self.enterContext(mock.patch.object(voice, "select", self.set_members))
        self.enterContext(mock.patch.object(voice, "environment"))
        self.enterContext(mock.patch.object(voice.os, "geteuid", return_value=1001))
        voice.MODEL.mkdir()
        (voice.MODEL / "test.xnet").write_bytes(b"model")
        (voice.MODEL / "dict.decoder.utf8.txt").write_bytes(b"dict")

    def set_members(self, _, values):
        self.members[:] = values

    def test_fresh_user_install_repeat_and_rollback(self):
        voice.main("apply", None)
        record = (voice.STATE / "backup/record.json").read_bytes()
        self.assertEqual(self.members, [True, False])
        voice.main("apply", None)
        self.assertEqual((voice.STATE / "backup/record.json").read_bytes(), record)
        voice.main("rollback", None)
        voice.main("rollback", None)
        self.assertFalse(voice.TARGET.exists())
        self.assertFalse(voice.CONFIG.exists())
        self.assertTrue(voice.model_ready())
        self.assertEqual(self.members, [False, True])

    def test_preserve_preferences_and_keyboard_and_other_updates(self):
        voice.TARGET.mkdir(parents=True)
        old = voice.TARGET / "extension.js"
        old.write_text("old")
        other = voice.TARGET / "other"
        other.write_text("extra")
        voice.CONFIG.parent.mkdir(parents=True)
        voice.CONFIG.write_text("Voice=False\nVoiceKey=F8\nPageSize=5\n[Hotkey]\nVoice=group\n")
        voice.main("apply", None)
        self.assertIn("VoiceKey=Alt_R\n", voice.CONFIG.read_text())
        voice.CONFIG.write_text(voice.CONFIG.read_text().replace("PageSize=5", "PageSize=7"))
        old.write_text("later update")
        with self.assertRaises(voice.Conflict):
            voice.main("rollback", None)
        self.assertEqual(old.read_text(), "later update")
        old.write_bytes((voice.SOURCE / "extension.js").read_bytes())
        voice.main("rollback", None)
        self.assertEqual(old.read_text(), "old")
        self.assertEqual(other.read_text(), "extra")
        self.assertEqual(voice.CONFIG.read_text(), "Voice=False\nVoiceKey=F8\nPageSize=7\n[Hotkey]\nVoice=group\n")

    def test_newer_keyboard_and_model_download_failure(self):
        voice.TARGET.mkdir(parents=True)
        meta = voice.TARGET / "metadata.json"
        meta.write_text(json.dumps({"uuid": voice.UUID, "version": 23}))
        with self.assertRaisesRegex(ValueError, "更新"):
            voice.main("apply", None)
        meta.unlink()
        (voice.MODEL / "test.xnet").unlink()
        with mock.patch.object(voice.subprocess, "run", side_effect=OSError("download")), self.assertRaises(OSError):
            voice.main("apply", None)
        self.assertFalse((voice.STATE / "backup").exists())
        self.assertEqual(list(voice.TARGET.iterdir()), [])

    def test_only_desktop_dependencies_checked_with_both_dt_identifiers(self):
        # Board recognition works with either the DT model or compatible token.
        settings = mock.Mock()
        settings.get_boolean.return_value = False
        for model, compatible in [(b"Lenovo TB710FU", b"qcom,sm8650"), (b"Lenovo", b"lenovo,tb710fu\0qcom,sm8650")]:
            def read(path):
                return compatible if path.name == "compatible" else model
            with mock.patch.object(Path, "read_bytes", read), mock.patch.object(Path, "is_file", return_value=True), \
                    mock.patch.object(voice.shutil, "which", return_value="/usr/bin/program"), \
                    mock.patch.object(voice.subprocess, "check_output", return_value="GNOME Shell 50.1") as run:
                # This test uses the real preflight function, unlike the installer transaction fixtures.
                self.original_environment(settings)
                run.assert_called_once_with(["gnome-shell", "--version"], text=True)

    original_environment = staticmethod(voice.environment)
