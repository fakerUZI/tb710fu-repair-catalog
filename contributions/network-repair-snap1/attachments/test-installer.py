#!/usr/bin/env python3
"""Fault-injection tests for guarded writes and complete rollback."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root/'package'
        self.base.mkdir()
        spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install-network-repair.py'))
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.__file__ = str(self.base/'install-network-repair.py')
        self.m.ROOT = root/'modules'
        self.m.BACKUP = root/'backup'
        self.m.LOAD_FILE = root/'modules-load.conf'
        self.paths = ['kernel/net/netfilter/x_tables.ko', 'kernel/net/netfilter/xt_nat.ko']
        modules = {}
        for i, rel in enumerate(self.paths):
            src = self.base/'modules'/rel
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b'new-'+str(i).encode())
            old = None
            if i == 0:
                dst = self.m.ROOT/rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b'original-module')
                old = self.m.digest(dst)
            modules[rel] = {'sha256': self.m.digest(src), 'before_sha256': old}
        self.m.LOAD_FILE.write_bytes(b'original-preload\n')
        (self.base/'modules-load.conf').write_bytes(b'new-preload\n')
        self.manifest = {'modules': modules, 'required_modules': ['x_tables','xt_nat'],
                         'modules_load_sha256': self.m.digest(self.base/'modules-load.conf')}
        (self.base/'manifest.json').write_text(json.dumps(self.manifest))
        self.m.MANIFEST_SHA256 = self.m.digest(self.base/'manifest.json')

    def invoke(self, action='apply'):
        with patch.object(sys, 'argv', ['installer', action]), \
             patch.object(self.m, 'target_checks'), \
             patch.object(self.m.subprocess, 'check_output', return_value=self.m.VERSION+' SMP aarch64'), \
             patch.object(self.m.subprocess, 'run', return_value=subprocess.CompletedProcess([],0)), \
             patch.object(self.m.os, 'sync'), patch.object(self.m.os, 'umask', return_value=0o22):
            self.m.main()

    def assert_original(self):
        self.assertEqual((self.m.ROOT/self.paths[0]).read_bytes(), b'original-module')
        self.assertFalse((self.m.ROOT/self.paths[1]).exists())
        self.assertEqual(self.m.LOAD_FILE.read_bytes(), b'original-preload\n')

    def test_corrupt_payload_rejected_before_writes(self):
        (self.base/'modules'/self.paths[1]).write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assert_original()
        self.assertFalse(self.m.BACKUP.exists())

    def test_manifest_change_rejected(self):
        (self.base/'manifest.json').write_text('{}')
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original()

    def test_unknown_existing_module_preserved(self):
        (self.m.ROOT/self.paths[0]).write_bytes(b'someone-elses-driver')
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertEqual((self.m.ROOT/self.paths[0]).read_bytes(), b'someone-elses-driver')
        self.assertFalse(self.m.BACKUP.exists())

    def test_partial_write_failure_restores_all_originals(self):
        real = self.m.replace
        failed = False
        def fail_second(source, destination):
            nonlocal failed
            if not failed and destination == self.m.ROOT/self.paths[1]:
                failed = True
                raise OSError('injected storage write failure')
            real(source, destination)
        with patch.object(self.m, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.invoke()
        self.assert_original()

    def test_success_idempotence_and_rollback(self):
        self.invoke()
        state = (self.m.BACKUP/'state.json').read_bytes()
        self.invoke()
        self.assertEqual((self.m.BACKUP/'state.json').read_bytes(), state)
        self.invoke('rollback')
        self.assert_original()

    def test_corrupt_backup_rejected_before_partial_rollback(self):
        self.invoke()
        before = (self.m.ROOT/self.paths[0]).read_bytes()
        (self.m.BACKUP/'modules'/self.paths[0]).write_bytes(b'broken-backup')
        with self.assertRaises(RuntimeError):
            self.invoke('rollback')
        self.assertEqual((self.m.ROOT/self.paths[0]).read_bytes(), before)
        self.assertTrue((self.m.ROOT/self.paths[1]).exists())

    def test_wrong_kernel_rejected(self):
        with patch.object(self.m.os, 'geteuid', return_value=0), \
             patch.object(self.m.os, 'uname', return_value=types.SimpleNamespace(machine='aarch64',release='wrong')):
            with self.assertRaises(RuntimeError):
                self.m.target_checks(self.manifest)

    def test_reviewed_upgrade_preserves_first_backup(self):
        self.invoke()
        state = (self.m.BACKUP/'state.json').read_bytes()
        rel = self.paths[0]
        entry = self.manifest['modules'][rel]
        entry['accepted_before_sha256'] = [entry['sha256']]
        source = self.base/'modules'/rel
        source.write_bytes(b'new-second-reviewed-revision')
        entry['sha256'] = self.m.digest(source)
        (self.base/'manifest.json').write_text(json.dumps(self.manifest))
        self.m.MANIFEST_SHA256 = self.m.digest(self.base/'manifest.json')
        self.invoke()
        self.assertEqual((self.m.BACKUP/'state.json').read_bytes(), state)
        self.invoke('rollback')
        self.assert_original()

    def test_private_umask_does_not_hide_module_indexes(self):
        p = self.m.ROOT/'modules.dep'
        p.write_text('module dependency index')
        p.chmod(0o600)
        with patch.object(self.m.subprocess, 'run', return_value=subprocess.CompletedProcess([],0)):
            previous = self.m.os.umask(0o077)
            try:
                self.m.depmod()
                self.assertEqual(p.stat().st_mode & 0o777, 0o644)
            finally:
                self.m.os.umask(previous)


if __name__ == '__main__':
    unittest.main()
