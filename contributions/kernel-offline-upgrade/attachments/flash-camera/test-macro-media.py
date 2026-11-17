#!/usr/bin/python3
import importlib.util,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('macro',Path(__file__).with_name('tb-macro-hardware.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Media(unittest.TestCase):
 def test_clean_stop_never_kills(self):
  calls=[]
  def ctl(uid,*args,**kw):calls.append(args);return subprocess.CompletedProcess(args,0,'inactive\n','')
  with patch.object(m,'userctl',ctl):m.stop_media(1000)
  self.assertFalse(any(a[0]=='kill' for a in calls));self.assertEqual(calls[0][:2],('stop','--no-block'))
 def test_only_stopping_service_can_be_killed(self):
  calls=[]
  def ctl(uid,*args,**kw):
   calls.append(args)
   return subprocess.CompletedProcess(args,0,'deactivating\n' if args[0]=='is-active' and args[1]=='pipewire-pulse.service' else 'inactive\n','')
  with patch.object(m,'userctl',ctl),patch.object(m.time,'monotonic',side_effect=[0,6]):m.stop_media(1000)
  killed=[a[-1] for a in calls if a[0]=='kill'];self.assertEqual(killed,['pipewire-pulse.service'])
 def test_failure_restores_original_units(self):
  calls=[]
  def ctl(uid,*args,**kw):calls.append(args);return subprocess.CompletedProcess(args,0,'active\n','')
  with patch.object(m,'userctl',ctl),patch.object(m.Path,'glob',return_value=[Path('/run/user/1000/bus')]),patch.object(m,'stop_media'):
   with self.assertRaises(RuntimeError):
    with m.pause_media():raise RuntimeError('failed probe')
  self.assertTrue(any(a[:2]==('start','--no-block') for a in calls))
if __name__=='__main__':unittest.main()
