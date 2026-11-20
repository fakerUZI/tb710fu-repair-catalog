import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ENTRY = Path(__file__).resolve().parents[1]/'contributions/full-virtual-keyboard'
SPEC = importlib.util.spec_from_file_location('repair_full_keyboard', ENTRY/'attachments/install.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class KeyboardRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.staged = self.root/'bundle'
        self.staged.mkdir()
        self.state = self.root/'state'
        self.state.mkdir()
        self.paths = [self.root/'user'/str(index) for index in range(len(repair.UUIDS)+3)]
        self.paths[0].mkdir(parents=True)
        (self.paths[0]/'previous.js').write_text('old version')
        self.paths[-1].write_text('[Behavior]\nPreeditEnabledByDefault=True\nOtherKey=keep\n')
        self.prefs = {
            'enabled-extensions': ['unrelated@local', 'tb-android-window-defaults@tb710fu'],
            'disabled-extensions': ['another@local', 'tb-android-outer-shadow@tb710fu'],
        }
        self.initial = [repair.fingerprint(path) for path in self.paths]
        command = repair.command
        def user_command(*args):
            if args == ('systemctl', '--user', 'daemon-reload'):
                return ''
            return command(*args)
        context = patch.multiple(repair, settings=self.prefs.__getitem__,
                                 set_settings=self.prefs.__setitem__, command=user_command)
        context.start()
        self.addCleanup(context.stop)
        if not shutil.which('glib-compile-schemas'):
            self.skipTest('glib-compile-schemas is needed to stage Kimpanel')
        repair.stage_bundle(self.staged)

    def test_bundle_matches_reviewable_source_and_contains_all_relative_imports(self):
        with ZipFile(ENTRY/'attachments/payload.zip') as payload:
            manifest = json.loads(payload.read('manifest.json'))
            source = ENTRY/'source'
            actual = {path.relative_to(source).as_posix() for path in source.rglob('*')
                      if path.is_file() and path.relative_to(source).parts[0] != 'translations'}
            self.assertEqual(set(manifest), actual)
            for name, expected in manifest.items():
                data = payload.read(name)
                self.assertEqual(data, (source/name).read_bytes())
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)
                if name.endswith('.js'):
                    for relative in re.findall(r'''from ['"](\./[^'"]+)['"]''', data.decode()):
                        self.assertIn(str(Path(name).parent/relative), manifest)
            self.assertFalse(any('tb-android-' in member for member in manifest))
        self.assertTrue((self.staged/'extensions/kimpanel@kde.org/schemas/gschemas.compiled').is_file())

    def test_apply_idempotence_and_rollback(self):
        repair.apply(self.staged, self.paths, self.state)
        pointer = (self.state/'latest').read_text()
        self.assertEqual(self.prefs['enabled-extensions'],
                         ['unrelated@local', 'tb-android-window-defaults@tb710fu', *repair.UUIDS])
        self.assertEqual(self.prefs['disabled-extensions'], ['another@local', 'tb-android-outer-shadow@tb710fu'])
        self.assertIn('PreeditEnabledByDefault=False', self.paths[-1].read_text())
        self.assertIn('OtherKey=keep', self.paths[-1].read_text())
        repair.apply(self.staged, self.paths, self.state)
        self.assertEqual((self.state/'latest').read_text(), pointer)
        self.prefs['enabled-extensions'].append('added-later@local')
        repair.rollback(self.state, self.paths)
        self.assertEqual([repair.fingerprint(path) for path in self.paths], self.initial)
        self.assertEqual(self.prefs['enabled-extensions'],
                         ['unrelated@local', 'tb-android-window-defaults@tb710fu', 'added-later@local'])
        self.assertIn('tb-android-outer-shadow@tb710fu', self.prefs['disabled-extensions'])

    def test_failure_midway_restores_original(self):
        real = repair.replace
        fail = self.paths[3]
        def fail_install(path, source):
            if path == fail and source is not None and source.parent.name == 'extensions':
                raise OSError('simulated install failure')
            return real(path, source)
        with patch.object(repair, 'replace', side_effect=fail_install), self.assertRaises(OSError):
            repair.apply(self.staged, self.paths, self.state)
        self.assertEqual([repair.fingerprint(path) for path in self.paths], self.initial)
        self.assertFalse((self.state/'latest').exists())
        self.assertNotIn(repair.UUIDS[-1], self.prefs['enabled-extensions'])

    def test_rollback_preserves_later_local_changes(self):
        repair.apply(self.staged, self.paths, self.state)
        (self.paths[0]/'local.js').write_text('new local content')
        with self.assertRaises(ValueError):
            repair.rollback(self.state, self.paths)
        self.assertTrue((self.paths[0]/'local.js').is_file())


if __name__ == '__main__':
    unittest.main()
