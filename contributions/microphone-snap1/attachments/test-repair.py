#!/usr/bin/env python3
"""Offline regression tests. No device access or real systemctl commands."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('repair', Path(__file__).with_name('repair.py'))
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.src = self.root / 'before'; self.out = self.root / 'after'
        self.src.write_bytes(b'A' * 8192)
        self.expected = self.root / 'expected'; self.expected.write_bytes(b'A' * 4096 + b'B' * 4096)
        self.before = r.sha(self.src); self.after = r.sha(self.expected)
        self.delta = dict(format=1, block_size=4096, size=8192, before_sha256=self.before,
                          after_sha256=self.after, ranges=[dict(offset=4096, data=base64.b64encode(b'B'*4096).decode())])
        for k, v in [('SIZE', 8192), ('BEFORE', self.before), ('AFTER', self.after)]:
            p = patch.object(r, k, v); p.start(); self.addCleanup(p.stop)
    def test_reconstruct_and_pin_final_digest(self):
        r.patch_image(self.src, self.out, self.delta)
        self.assertEqual(self.out.read_bytes(), self.expected.read_bytes())
    def test_wrong_baseline_rejected_before_output(self):
        self.src.write_bytes(b'C'*8192)
        with self.assertRaises(r.Stop): r.patch_image(self.src, self.out, self.delta)
        self.assertFalse(self.out.exists())
    def test_outside_range_rejected_before_output(self):
        self.delta['ranges'][0]['offset'] = 8192
        with self.assertRaises(r.Stop): r.patch_image(self.src, self.out, self.delta)
        self.assertFalse(self.out.exists())
    def test_overlapping_ranges_rejected(self):
        self.delta['ranges'].append(self.delta['ranges'][0])
        with self.assertRaises(r.Stop): r.patch_image(self.src, self.out, self.delta)
        self.assertFalse(self.out.exists())
    def test_corrupt_payload_never_yields_accepted_image(self):
        self.delta['ranges'][0]['data'] = base64.b64encode(b'C'*4096).decode()
        with self.assertRaises(r.Stop): r.patch_image(self.src, self.out, self.delta)
    def test_symlinked_configuration_rejected(self):
        dest = self.root / 'link'; dest.symlink_to(self.src)
        with self.assertRaises(r.Stop): r.atomic(dest, b'bad')
        self.assertEqual(self.src.read_bytes(), b'A'*8192)
    def test_restore_absent_and_existing_files(self):
        existing = self.root / 'existing'; absent = self.root / 'new-file'
        r.atomic(self.root / 'config-0', b'original')
        existing.write_bytes(b'changed'); absent.write_bytes(b'new')
        state = dict(enabled=False, files={str(existing):dict(backup='config-0',sha256=r.sha(self.root/'config-0'),mode=0o640),str(absent):None})
        with patch.object(r, 'run') as run:
            r.restore_files(self.root, state)
        self.assertEqual(existing.read_bytes(), b'original'); self.assertFalse(absent.exists())
        self.assertEqual(existing.stat().st_mode & 0o777, 0o640)
        self.assertEqual(run.call_args_list[0].args, (['systemctl','disable',r.UNIT],False))
    def test_device_identity_change_rejected(self):
        a=dict(root_uuid='local',firmware={'abl':'same'},gpt={})
        b={**a,'root_uuid':'other'}
        with self.assertRaises(r.Stop):r.same_device(a,b)
    def test_normalization_keeps_geometry_and_strips_identifiers(self):
        a={'/dev/sda':{'partitiontable':dict(device='/dev/sda',id='private',sectorsize=4096,partitions=[dict(node='/dev/sda1',uuid='private',name='boot_a',start=100,size=20,type='public-type',attrs='GUID:50,51,54')])}}
        n=r.normalize(a);self.assertNotIn('private',json.dumps(n));self.assertEqual(n[0]['partitions'][0]['attrs'],'GUID:50')
        a['/dev/sda']['partitiontable']['partitions'][0]['start']=101
        self.assertNotEqual(n,r.normalize(a))


    def transaction(self):
        if os.geteuid() != 0: self.skipTest('transaction sandbox is run as root inside Docker')
        inv=dict(boot=str(self.src),rdev=self.src.stat().st_rdev,boot_sha256=self.before,
                 root_uuid='synthetic-root',firmware={},gpt={})
        (self.root/'boot-delta.json').write_text(json.dumps(self.delta))
        (self.root/'init-source').write_bytes(b'new init')
        (self.root/'unit-source').write_bytes(b'new unit')
        old=self.root/'system/old';old.parent.mkdir();old.write_bytes(b'old init')
        new=self.root/'system/new'
        for name,value in [('BASE',self.root/'backups'),('HERE',self.root),
                           ('FILES',{str(old):('init-source',0o755),str(new):('unit-source',0o644)})]:
            p=patch.object(r,name,value);p.start();self.addCleanup(p.stop)
        run=patch.object(r,'run');run.start();self.addCleanup(run.stop)
        def current(compat): return {**inv,'boot_sha256':r.sha(self.src)}
        p=patch.object(r,'inventory',side_effect=current);p.start();self.addCleanup(p.stop)
        return inv,old,new
    def test_apply_and_rollback_transaction_on_fake_block_file(self):
        inv,old,new=self.transaction()
        r.apply({},inv)
        self.assertEqual(r.sha(self.src),self.after)
        self.assertEqual(old.read_bytes(),b'new init');self.assertEqual(new.read_bytes(),b'new unit')
        r.rollback({},r.inventory({}))
        self.assertEqual(r.sha(self.src),self.before)
        self.assertEqual(old.read_bytes(),b'old init');self.assertFalse(new.exists())
        self.assertFalse((r.BASE/'active.json').exists())
    def test_partial_write_failure_restores_boot_and_config(self):
        inv,old,new=self.transaction()
        def fail(*args):
            self.src.write_bytes(b'partial')
            raise r.Stop('simulated write failure')
        with patch.object(r,'write_boot',side_effect=fail):
            with self.assertRaises(r.Stop):r.apply({},inv)
        self.assertEqual(r.sha(self.src),self.before)
        self.assertEqual(old.read_bytes(),b'old init');self.assertFalse(new.exists())
        self.assertFalse((r.BASE/'active.json').exists())
    def test_idempotent_apply_does_not_make_another_backup(self):
        inv,old,new=self.transaction();r.apply({},inv)
        before=list(r.BASE.glob('repair-*'))
        r.apply({},r.inventory({}))
        self.assertEqual(before,list(r.BASE.glob('repair-*')))
    def test_modified_config_prevents_rollback_without_overwrite(self):
        inv,old,new=self.transaction();r.apply({},inv);old.write_bytes(b'later edit')
        with self.assertRaises(r.Stop):r.rollback({},r.inventory({}))
        self.assertEqual(old.read_bytes(),b'later edit');self.assertEqual(r.sha(self.src),self.after)

if __name__=='__main__': unittest.main()
