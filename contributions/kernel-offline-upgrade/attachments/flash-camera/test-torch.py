import importlib.util,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('torch',Path(__file__).with_name('tb-torch-daemon.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Timer(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'brightness').write_text('99');(self.root/'flash_fault').write_text('');self.now=0
  self.t=m.Torch(self.root,60,lambda:self.now)
 def tearDown(self):self.temp.cleanup()
 def test_starts_off(self):self.assertEqual(self.t.state(),(False,0))
 def test_on_is_current_limited(self):self.t.set(True);self.assertEqual((self.root/'brightness').read_text(),'128');self.assertEqual(self.t.state(),(True,60))
 def test_deadline_turns_off(self):
  self.t.set(True);self.now=59.2;self.assertEqual(self.t.state(),(True,1));self.now=60;self.assertEqual(self.t.state(),(False,0));self.assertEqual((self.root/'brightness').read_text(),'0')
 def test_repeated_on_cannot_extend_deadline(self):self.t.set(True);self.now=59;self.t.set(True);self.now=60;self.assertEqual(self.t.state(),(False,0))
 def test_explicit_off(self):self.t.set(True);self.t.set(False);self.assertEqual(self.t.state(),(False,0))
 def test_hardware_fault_stays_off(self):
  (self.root/'flash_fault').write_text('short-circuit')
  with self.assertRaises(RuntimeError):self.t.set(True)
  self.assertEqual(self.t.state(),(False,0))
 def test_unknown_external_enable_is_disabled(self):
  (self.root/'brightness').write_text('255');self.assertEqual(self.t.state(),(False,0));self.assertEqual((self.root/'brightness').read_text(),'0')
 def test_off_can_clear_even_with_fault(self):
  self.t.set(True);(self.root/'flash_fault').write_text('over-temperature');self.t.set(False);self.assertEqual(self.t.state(),(False,0))
if __name__=='__main__':unittest.main()
