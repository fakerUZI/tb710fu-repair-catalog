import importlib.util,importlib.machinery,tempfile,stat,sys
from pathlib import Path
import gi
gi.require_version('GdkPixbuf','2.0')
from gi.repository import GdkPixbuf
source=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name('tb-camera.py')
spec=importlib.util.spec_from_file_location('rear_camera',source,loader=importlib.machinery.SourceFileLoader('rear_camera',str(source)));app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
def jpeg(color):
 p=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,16,12);p.fill(color)
 ok,data=p.save_to_bufferv('jpeg',['quality'],['90']);assert ok;return data
black,red,blue=(jpeg(c) for c in [0x000000ff,0xff0000ff,0x0000ffff])
assert not app.jpeg_has_image(black);assert app.jpeg_has_image(red);assert app.jpeg_has_image(blue)
class Buffer:
 def __init__(self,data):self.data=data
 def get_size(self):return len(self.data)
 def extract_dup(self,*args):return self.data
class Sample:
 def __init__(self,data):self.data=data
 def get_buffer(self):return Buffer(self.data)
class Sink:
 def __init__(self):self.data=[black,blue];self.frames=0
 def emit(self,*args):self.frames+=1;return Sample(self.data.pop(0))
class Pipeline:
 def __init__(self):self.sink=Sink()
 def get_by_name(self,name):assert name=='jpeg';return self.sink
class Gst:SECOND=1000000000
p=Pipeline();data=app.pull_ready_photo(Gst,p)
assert data==blue and p.sink.frames==2,'Must ignore blank JPEG output; sensor warmup is handled before encoding'
try:app.pull_ready_photo(Gst,p,timeout_seconds=0)
except RuntimeError:pass
else:raise AssertionError('Timeout must not return an empty image')
with tempfile.TemporaryDirectory() as folder:
 dest=Path(folder)/'photo.jpg';app.save_photo(dest,data,(16,12));assert dest.read_bytes()==blue
 assert stat.S_IMODE(dest.stat().st_mode)==0o600
 try:app.save_photo(dest,data,(4200,3120))
 except ValueError:pass
 else:raise AssertionError('Wrong-size JPEG accepted')
 assert dest.read_bytes()==blue
 assert list(Path(folder).iterdir())==[dest]
print('Blank JPEG rejection, timeout, dimensions, atomic save and private permissions passed')
