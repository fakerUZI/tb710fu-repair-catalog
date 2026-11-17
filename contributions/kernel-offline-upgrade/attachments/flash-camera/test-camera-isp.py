#!/usr/bin/python3
import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import subprocess
spec=importlib.util.spec_from_file_location('isp',Path(__file__).with_name('install-camera-isp.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ISP(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.base=Path(self.t.name)/'backup';self.calls=[];self.v={n:m.OLD for n in m.NAMES}
  def run(*args,**kwargs):
   self.calls.append(args)
   if args[:2]==('dpkg','--install'):
    version=m.NEW if m.NEW in args[2] else m.OLD
    self.v.update({n:version for n in m.NAMES})
   return subprocess.CompletedProcess(args,0,'','')
  self.patches=[patch.object(m,'BASE',self.base),patch.object(m,'run',run),patch.object(m,'versions',lambda:dict(self.v)),patch.object(m.addon,'package',lambda *a:None),patch.object(m.addon,'hardware_check',lambda *a:None),patch.object(m.addon,'save_payload',lambda *a:None)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.t.cleanup()
 def test_unknown_version_blocks_before_dpkg(self):
  self.v[m.NAMES[0]]='unknown'
  with self.assertRaises(RuntimeError):m.check()
  self.assertFalse(any(a[:2]==('dpkg','--install') for a in self.calls))
 def test_modified_files_refused(self):
  def run(*args,**kwargs):return subprocess.CompletedProcess(args,0,'modified' if args[:2]==('dpkg','--verify') else '','')
  with patch.object(m,'run',run):
   with self.assertRaises(RuntimeError):m.check()
 def test_failed_smoke_reverts_stock_and_records_state(self):
  with patch.object(m,'check',lambda:None),patch.object(m,'smoke',side_effect=RuntimeError('capture failed')):
   with self.assertRaises(RuntimeError):m.apply()
  self.assertTrue(all(v==m.OLD for v in self.v.values()));self.assertEqual(json.loads((self.base/'state.json').read_text())['phase'],'rolled-back')
  self.assertTrue(any(a[0]=='systemd-run' for a in self.calls))
  self.assertTrue(any(a[:2]==('systemctl','stop') for a in self.calls))
 def test_success_disarms_timer_and_keeps_snapshot(self):
  with patch.object(m,'check',lambda:None),patch.object(m,'smoke',lambda:None):m.apply()
  self.assertTrue(all(v==m.NEW for v in self.v.values()));self.assertEqual(json.loads((self.base/'state.json').read_text())['phase'],'complete')
 def test_other_upgrade_blocks_rollback(self):
  self.base.mkdir();(self.base/'state.json').write_text(json.dumps({'old_version':m.OLD,'phase':'complete'}));self.v[m.NAMES[0]]='other'
  with self.assertRaises(RuntimeError):m.rollback()
  self.assertFalse(any(a[:2]==('dpkg','--install') for a in self.calls))
if __name__=='__main__':unittest.main()
