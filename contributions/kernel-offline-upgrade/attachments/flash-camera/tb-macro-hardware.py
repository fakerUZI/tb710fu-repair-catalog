#!/usr/bin/python3
"""Version-pinned GC02M1 add-on; preserve and rebuild the existing camera graph."""
import contextlib,hashlib,json,os,pwd,subprocess,sys,time
from pathlib import Path
VERSION='7.2.6-tb710fu-20261117'
ROOT=Path('/opt/tb710fu-macro')/VERSION
CAMSS=Path('/opt/tb710fu-camera-test')/VERSION/'qcom-camss-graph-UNTESTED.ko'
UNITS=('wireplumber.service','pipewire-pulse.service','pipewire-pulse.socket','pipewire.service','pipewire.socket')
def run(*args,check=True,timeout=30):
 return subprocess.run(args,check=check,text=True,capture_output=True,timeout=timeout)
def require(ok,message):
 if not ok:raise RuntimeError(message)
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def check():
 require(os.uname().release==VERSION,'运行内核不匹配')
 run('python3','/usr/local/libexec/tb-flash-hardware.py','check',timeout=75)
 m=json.loads((ROOT/'macro-manifest.json').read_text())
 for name,digest in m['files'].items():
  require(Path(name).name==name and sha(ROOT/name)==digest,'微距文件校验失败：'+name)
 require(sha('/lib/firmware/tb-macro-raw.dtbo')==m['files']['tb-macro-raw.dtbo'],'微距设备树不匹配')
 for name in ('tb_macro_registration.ko','gc02m1_raw.ko'):
  require(run('modinfo','-F','vermagic',str(ROOT/name)).stdout.startswith(VERSION+' '),'微距模块 ABI 不匹配')
 require(sha(CAMSS)==m['camss_sha256'],'原有 CAMSS 发生变化')
def userctl(uid,*args,check=True):
 user=pwd.getpwuid(uid).pw_name
 return run('runuser','-u',user,'--','env','XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus','systemctl','--user',*args,check=check)
def stop_media(uid):
 userctl(uid,'stop','--no-block',*UNITS)
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  states={u:userctl(uid,'is-active',u,check=False).stdout.strip() for u in UNITS}
  if all(v in ('inactive','failed','unknown') for v in states.values()):return
  time.sleep(.2)
 # A hung PulseAudio compatibility process can otherwise keep a stop job alive
 # for 90 seconds. Kill only a service already stopping in this user's manager.
 for unit in UNITS:
  if unit.endswith('.service') and userctl(uid,'is-active',unit,check=False).stdout.strip()=='deactivating':
   userctl(uid,'kill','--kill-whom=all','--signal=SIGKILL',unit,check=False)
 userctl(uid,'stop',*UNITS)

@contextlib.contextmanager
def pause_media():
 active=[]
 try:
  for bus in sorted(Path('/run/user').glob('*/bus')):
   uid=int(bus.parent.name)
   if uid==0:continue
   units=[u for u in UNITS if userctl(uid,'is-active',u,check=False).stdout.strip() in ('active','activating','deactivating')]
   if units:
    active.append((uid,units));stop_media(uid)
  yield
 finally:
  for uid,units in active:userctl(uid,'start','--no-block',*units)
def unload(name):
 if Path('/sys/module',name).exists():run('rmmod',name)
def reload_camss():
 if not Path('/sys/module/qcom_camss').exists():run('insmod',str(CAMSS))
def start():
 check()
 if Path('/sys/module/gc02m1_raw').exists():
  require('gc02m1_raw' in run('cam','-l').stdout,'微距模块已加载但未枚举');return
 with pause_media():
  unload('qcom_camss')
  try:
   run('insmod',str(ROOT/'tb_macro_registration.ko'),'overlay_name=tb-macro-raw.dtbo')
   run('insmod',str(ROOT/'gc02m1_raw.ko'))
   reload_camss()
   ready=False
   for _ in range(30):
    if 'gc02m1_raw' in run('cam','-l',timeout=5).stdout:ready=True;break
    time.sleep(.2)
   require(ready,'微距没有完成枚举')
  except BaseException:
   unload('qcom_camss');unload('gc02m1_raw');unload('tb_macro_registration');reload_camss();raise
 print('GC02M1 micro camera ready; existing rear/front preserved')
def stop():
 if not Path('/sys/module/tb_macro_registration').exists():return
 with pause_media():
  unload('qcom_camss')
  try:
   unload('gc02m1_raw');unload('tb_macro_registration')
  finally:reload_camss()
if __name__=='__main__':
 try:
  require(os.geteuid()==0,'需要管理员权限')
  {'check':check,'start':start,'stop':stop}[sys.argv[1] if len(sys.argv)>1 else 'check']()
 except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as exc:raise SystemExit('停止：'+str(exc))
