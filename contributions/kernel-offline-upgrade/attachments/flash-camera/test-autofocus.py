#!/usr/bin/python3
import importlib.util, math, pathlib, unittest
spec=importlib.util.spec_from_file_location('camera',pathlib.Path(__file__).with_name('tb-camera.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class AFTests(unittest.TestCase):
 def sweep(self,curve):
  writes=[];result=[]
  af=c.ContrastFocus(700,writes.append,lambda *v:result.append(v),now=0)
  for tick in range(1,800):
   af.frame(curve(af.position),now=tick*.07)
   if not af.active:break
  self.assertFalse(af.active);self.assertTrue(all(0<=v<=1023 for v in writes))
  return writes,result[0]
 def test_find_peak_away_from_fixed_focus(self):
  writes,result=self.sweep(lambda p:.01+.2*math.exp(-((p-360)/85)**2))
  self.assertTrue(result[0]);self.assertLessEqual(abs(result[1]-360),24)
 def test_already_focused(self):
  _,r=self.sweep(lambda p:.01+.2*math.exp(-((p-700)/85)**2));self.assertTrue(r[0]);self.assertEqual(r[1],700)
 def test_flat_scene_restores(self):
  w,r=self.sweep(lambda p:.01);self.assertFalse(r[0]);self.assertEqual(w[-1],700)
 def test_no_texture_restores(self):
  w,r=self.sweep(lambda p:None);self.assertFalse(r[0]);self.assertEqual(w[-1],700)
 def test_noisy_peak_confirmation_restores(self):
  w=[];r=[];af=c.ContrastFocus(700,w.append,lambda *v:r.append(v),now=0)
  for tick in range(1,800):
   af.frame(.001 if af.phase=='verify' else .01+.2*math.exp(-((af.position-360)/85)**2),now=tick*.07)
   if not af.active:break
  self.assertFalse(r[0][0]);self.assertEqual(w[-1],700)
 def test_cancel_restores(self):
  w=[];r=[];af=c.ContrastFocus(700,w.append,lambda *v:r.append(v),now=0);af.move(128,0);af.cancel();self.assertEqual(w[-1],700);self.assertFalse(r[0][0])
 def test_invalid_initial(self):
  with self.assertRaises(ValueError):c.ContrastFocus(-1,lambda v:None,lambda *v:None)
 def test_dark_blank_metric(self):
  self.assertIsNone(c.focus_metric(bytes([0,0,0,255])*128*96,128,96,512))
 def test_blur_reduces_metric(self):
  def frame(amplitude):
   b=bytearray()
   for y in range(96):
    for x in range(128):
     v=int(128+amplitude*math.sin(x*.7)*math.cos(y*.6));b.extend([v,v,v,255])
   return b
  self.assertGreater(c.focus_metric(frame(80),128,96,512),c.focus_metric(frame(20),128,96,512)*8)
if __name__=='__main__':unittest.main()
