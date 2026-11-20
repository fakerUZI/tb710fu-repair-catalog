import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


INSTALLER = (Path(__file__).resolve().parents[1]
             / 'contributions/terminal-hostname-fix/attachments/install.py')
SPEC = importlib.util.spec_from_file_location('terminal_hostname_fix', INSTALLER)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class TerminalHostnameFixTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hosts = directory / 'hosts'
        self.backup = directory / 'backup'
        self.backup.mkdir(mode=0o700)
        self.state = self.backup / 'before.json'
        self.original = b'127.0.0.1 localhost\r\n# missing final newline'
        self.hosts.write_bytes(self.original)
        self.hosts.chmod(0o640)
        self.enterContext(patch.multiple(repair, HOSTS=self.hosts, BACKUP=self.backup))

    def test_repeatable_apply_and_exact_rollback(self):
        repair.apply('tablet-fixture')
        self.assertEqual(self.hosts.read_bytes(), self.original + b'\n127.0.1.1\ttablet-fixture\n')
        self.assertEqual(stat.S_IMODE(self.hosts.stat().st_mode), 0o640)
        before = self.state.read_bytes()
        inode = self.hosts.stat().st_ino
        repair.apply('TABLET-FIXTURE')
        self.assertEqual(self.hosts.stat().st_ino, inode)
        self.assertEqual(self.state.read_bytes(), before)
        repair.rollback()
        self.assertEqual(self.hosts.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.hosts.stat().st_mode), 0o640)
        self.assertFalse(self.state.exists())

    def test_rollback_preserves_later_edits(self):
        repair.apply('tablet-fixture')
        modified = self.hosts.read_bytes() + b'# later edit\n'
        self.hosts.write_bytes(modified)
        with self.assertRaisesRegex(ValueError, '应用后另有改动'):
            repair.rollback()
        self.assertEqual(self.hosts.read_bytes(), modified)
        self.assertTrue(self.state.exists())

    def test_failed_replacement_can_be_retried(self):
        real_replace = os.replace

        def fail_for_hosts(source, target):
            if target == self.hosts:
                raise OSError('simulated failure')
            return real_replace(source, target)

        with patch.object(repair.os, 'replace', side_effect=fail_for_hosts):
            with self.assertRaisesRegex(OSError, 'simulated failure'):
                repair.apply('tablet-fixture')
        self.assertEqual(self.hosts.read_bytes(), self.original)
        self.assertEqual(list(self.hosts.parent.glob('.hosts.*')), [])
        self.assertTrue(self.state.exists())
        repair.apply('tablet-fixture')
        repair.rollback()
        self.assertEqual(self.hosts.read_bytes(), self.original)

    def test_existing_conflicting_alias_is_preserved(self):
        other_mapping = b'192.0.2.1 other TABLET-FIXTURE # local mapping\n'
        self.hosts.write_bytes(other_mapping)
        with self.assertRaisesRegex(ValueError, '人工确认'):
            repair.apply('tablet-fixture')
        self.assertEqual(self.hosts.read_bytes(), other_mapping)
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
