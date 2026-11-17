#!/usr/bin/python3
"""Record only frame luminance statistics; no images are saved."""
import gi,json,subprocess,time
from statistics import mean,stdev
gi.require_version('Gst','1.0')
from gi.repository import Gst
Gst.init(None)
p=Gst.parse_launch('libcamerasrc name=camera ! video/x-raw,format=RGBA,width=640,height=480 ! appsink name=frames sync=false drop=true max-buffers=1')
p.get_by_name('camera').set_property('camera-name','/base/soc@0/cci@ac15000/i2c-bus@1/camera@36')
sink=p.get_by_name('frames')
def toggle(value):
 subprocess.run(['gdbus','call','--system','--dest','org.tb710fu.Torch1','--object-path','/org/tb710fu/Torch1','--method','org.tb710fu.Torch1.SetEnabled',str(value).lower()],check=True,capture_output=True)
def frames(count):
 result=[]
 for _ in range(count):
  s=sink.emit('try-pull-sample',4*Gst.SECOND)
  if s is None:raise RuntimeError('No image')
  b=s.get_buffer();data=b.extract_dup(0,b.get_size());g=data[1::4][::23];result.append(round(sum(g)/len(g),3))
 return result
results={}
try:
 toggle(False);p.set_state(Gst.State.PLAYING);frames(12)
 for label,on in [('off_before',False),('on',True),('off_after',False)]:
  toggle(on);results[label]=frames(12)
finally:
 toggle(False);p.set_state(Gst.State.NULL);p.get_state(3*Gst.SECOND)
print(json.dumps({'green_luminance_samples':results,'means':{key:round(mean(value),3) for key,value in results.items()}},sort_keys=True))
