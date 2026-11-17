import hashlib,importlib.util,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('install-flash-camera.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def digest(data):return hashlib.sha256(data).hexdigest()
class Install(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'src';self.source.mkdir();self.state=self.root/'state';self.target=self.root/'app';self.target.write_bytes(b'original');self.target.chmod(0o640)
  (self.source/'app.py').write_bytes(b'new');self.mapping={'app.py':str(self.target)};self.ext=[]
  self.manifest={'files':{'app.py':digest(b'new')},'accepted':{str(self.target):[digest(b'original')]}}
  self.calls=[]
  def run(*args,check=True):
   self.calls.append(args);rc=1 if args[:2] in [('systemctl','is-active'),('systemctl','is-enabled')] else 0
   return subprocess.CompletedProcess(args,rc,'','')
  def setting(user,*args):
   if args[0]=='set':self.ext[:]=__import__('ast').literal_eval(args[-1])
   return repr(self.ext)
  self.patches=[patch.object(m,'BASE',self.state),patch.object(m,'MAP',self.mapping),patch.object(m,'hardware_check',lambda *a:None),patch.object(m,'extensions',lambda user:list(self.ext)),patch.object(m,'settings',setting),patch.object(m,'reload_bus',lambda:None),patch.object(m,'save_payload',lambda source:None),patch.object(m,'run',run)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def apply(self):m.apply(self.source,self.manifest,'fixture')
 def test_apply_rollback_restores_content_mode_and_extension(self):
  self.apply();self.assertEqual(self.target.read_bytes(),b'new');self.assertEqual(self.ext,[m.EXT]);m.rollback();self.assertEqual(self.target.read_bytes(),b'original');self.assertEqual(self.target.stat().st_mode&0o777,0o640);self.assertEqual(self.ext,[])
 def test_repeat_preserves_first_snapshot(self):
  self.apply();before=(self.state/'before-0').read_bytes();self.apply();self.assertEqual((self.state/'before-0').read_bytes(),before)
 def test_reviewed_revision_preserves_first_snapshot(self):
  self.apply();(self.source/'app.py').write_bytes(b'revision2');self.manifest['files']['app.py']=digest(b'revision2');self.apply();self.assertEqual((self.state/'before-0').read_bytes(),b'original');m.rollback();self.assertEqual(self.target.read_bytes(),b'original')
 def test_reapply_after_rollback_keeps_first_snapshot(self):
  self.apply();m.rollback();self.apply();self.assertEqual((self.state/'before-0').read_bytes(),b'original');self.assertEqual(self.target.read_bytes(),b'new')
 def test_user_edit_blocks_upgrade_and_rollback(self):
  self.apply();self.target.write_bytes(b'user change')
  for action in [self.apply,m.rollback]:
   with self.assertRaises(RuntimeError):action()
  self.assertEqual(self.target.read_bytes(),b'user change')
 def test_corrupt_backup_blocks_rollback_before_mutation(self):
  self.apply();(self.state/'before-0').write_bytes(b'bad');self.calls=[]
  with self.assertRaises(RuntimeError):m.rollback()
  self.assertEqual(self.target.read_bytes(),b'new');self.assertEqual(self.calls,[])
 def test_unknown_existing_file_rejected(self):
  self.target.write_bytes(b'unknown')
  with self.assertRaises(RuntimeError):self.apply()
  self.assertFalse(self.state.exists())
 def test_symlink_rejected(self):
  self.target.unlink();outside=self.root/'outside';outside.write_bytes(b'private');self.target.symlink_to(outside)
  with self.assertRaises(RuntimeError):self.apply()
  self.assertEqual(outside.read_bytes(),b'private')
 def test_interrupted_write_restores_all_originals(self):
  second=self.root/'second';second.write_bytes(b'old2');self.mapping['b.py']=str(second);(self.source/'b.py').write_bytes(b'new2');self.manifest['files']['b.py']=digest(b'new2');self.manifest['accepted'][str(second)]=[digest(b'old2')]
  original=m.atomic
  def fail(p,data,mode):
   if Path(p)==second and data==b'new2':raise OSError('simulated interruption')
   return original(p,data,mode)
  with patch.object(m,'atomic',fail):
   with self.assertRaises(OSError):self.apply()
  self.assertEqual(self.target.read_bytes(),b'original');self.assertEqual(second.read_bytes(),b'old2')
if __name__=='__main__':
 assert os.geteuid()==0,'Run in disposable root container'
 unittest.main()
