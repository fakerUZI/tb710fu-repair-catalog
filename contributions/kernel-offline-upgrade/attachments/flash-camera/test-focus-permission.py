import importlib.util,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('focus',Path(__file__).with_name('tb-camera-focus-permission.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Access(unittest.TestCase):
 def check_uid(self,uid):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'position';p.write_text('700')
   with patch.object(m,'FOCUS',p),patch.object(m,'active_uid',return_value=uid),patch.object(m.os,'uname',return_value=types.SimpleNamespace(release=m.VERSION)),patch.object(m.os,'geteuid',return_value=0),patch.object(m.os,'chown') as chown:
    m.main();chown.assert_called_once_with(p,uid,-1);self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_inactive_session_keeps_root(self):self.check_uid(0)
 def test_active_session_gets_lens_only(self):self.check_uid(1000)
 def test_other_kernel_does_not_write(self):
  with patch.object(m.os,'uname',return_value=types.SimpleNamespace(release='other')),patch.object(m.os,'chown') as chown:m.main();chown.assert_not_called()
if __name__=='__main__':unittest.main()
