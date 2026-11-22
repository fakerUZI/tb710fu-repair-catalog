import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


CONTRIBUTIONS = Path(__file__).resolve().parents[1] / "contributions"


def load_installer(name):
    path = CONTRIBUTIONS / name / "attachments/install.py"
    spec = importlib.util.spec_from_file_location(name, path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer


class MissingInstallDirectories(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backups = self.root / "var/backups"

    def test_screen_gestures_creates_backup_and_restores_removed_parent(self):
        gestures = load_installer("screen-gestures")
        backup = self.backups / "tb710fu-screen-gestures"
        original = self.root / "existing/original"
        original.parent.mkdir()
        original.write_bytes(b"original gestures")
        permissions = self.root / "permissions"
        permissions.write_bytes(b"original permissions")
        target = self.root / "usr/local/share/applications/tb710fu-gesture-settings.desktop"
        self.enterContext(patch.multiple(
            gestures, BASE=backup, DING=original, PERMISSIONS=permissions,
            needs_modules=lambda _spec: False, has_settings_version=lambda _spec: False,
            desktop_account=lambda: "tablet-user", boot_id=lambda: "boot-fixture",
            verify_payload=lambda *_args: {target: (b"launcher", 0o644)},
            gesture_edit=lambda *_args: (b"updated gestures", 0o644),
            global_wake_enabled=lambda: False, run=lambda *_args: "",
            indexes=lambda _drivers: None, clean_units=lambda _state: None,
        ))
        self.enterContext(patch.object(gestures.os, "access", return_value=True))
        self.enterContext(patch.object(gestures.os, "sync", create=True))

        gestures.install(check=True)
        self.assertFalse(self.backups.exists())
        self.assertFalse(target.parent.exists())

        gestures.install()
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(target.read_bytes(), b"launcher")
        self.assertEqual(original.read_bytes(), b"updated gestures")
        receipt = json.loads((backup / "state.json").read_text())
        self.assertEqual((backup / receipt["files"][str(original)]["original"]).read_bytes(),
                         b"original gestures")

        original.unlink()
        original.parent.rmdir()
        gestures.rollback()
        self.assertEqual(original.read_bytes(), b"original gestures")
        self.assertEqual(permissions.read_bytes(), b"original permissions")
        self.assertFalse(target.exists())

    def test_face_unlock_creates_private_backup_and_state_on_apply(self):
        face = load_installer("face-unlock")
        backup = self.backups / "tb710fu-face-unlock-catalog"
        state = self.root / "var/lib/tb710fu-face-unlock"
        prefix = self.root / "usr/local/lib/tb710fu-face-unlock"
        attachments = self.root / "attachments"
        attachments.mkdir()
        name = "face-unlock.py"
        helpers = {name: b"new face helper", "pam-face-unlock": b"pam helper", "manage": b"manage helper"}
        for helper, data in helpers.items():
            (attachments / helper).write_bytes(data)
        prefix.mkdir(parents=True)
        (prefix / name).write_bytes(b"original helper")
        files = {key: (prefix / key, 0o644) for key in (*helpers, *face.MODELS)}
        self.enterContext(patch.multiple(
            face, HERE=attachments, PREFIX=prefix, BACKUP=backup, STATE=state,
            FILES=files, preflight=lambda _apply: None, model=lambda key: key.encode(),
        ))
        self.enterContext(patch.object(face.subprocess, "run"))

        face.install(apply=False)
        self.assertFalse(self.backups.exists())
        self.assertFalse(state.exists())
        face.install(apply=True)
        for path in (backup, state):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual((backup / name).read_bytes(), b"original helper")
        self.assertEqual((prefix / name).read_bytes(), b"new face helper")

        receipt = (backup / "state.json").read_bytes()
        state.rmdir()
        face.install(apply=True)
        self.assertTrue(state.is_dir())
        self.assertEqual((backup / "state.json").read_bytes(), receipt)

        face.rollback()
        self.assertEqual((prefix / name).read_bytes(), b"original helper")
        self.assertFalse(backup.exists())

    def test_hostname_apply_creates_backup_and_lock_from_empty_var(self):
        repair = load_installer("terminal-hostname-fix")
        hosts = self.root / "hosts"
        hosts.write_bytes(b"127.0.0.1 localhost\n")
        backup = self.backups / "tb710fu-terminal-hostname-fix"
        release = self.root / "os-release"
        release.write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        model = self.root / "model"
        model.write_bytes(b"Lenovo TB710FU")
        system_paths = {"/etc/os-release": release, "/sys/firmware/devicetree/base/model": model}
        self.enterContext(patch.multiple(repair, HOSTS=hosts, BACKUP=backup,
                                         Path=lambda path: system_paths.get(path, Path(path))))
        self.enterContext(patch.object(repair.os, "uname", return_value=SimpleNamespace(machine="aarch64")))
        self.enterContext(patch.object(repair.os, "geteuid", return_value=0))
        self.enterContext(patch.object(repair, "hostname", return_value="tablet-fixture"))
        lstat = Path.lstat

        def root_owned_backup(path):
            info = lstat(path)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0) if path == backup else info

        self.enterContext(patch.object(type(backup), "lstat", root_owned_backup))
        repair.main("check")
        self.assertFalse(self.backups.exists())
        repair.main("apply")
        self.assertTrue((backup / "lock").is_file())
        self.assertTrue((backup / "before.json").is_file())
        self.assertIn(b"127.0.1.1\ttablet-fixture", hosts.read_bytes())
        repair.main("rollback")
        self.assertEqual(hosts.read_bytes(), b"127.0.0.1 localhost\n")
