#!/usr/bin/python3
"""Version-pinned flashlight and camera UI add-on. Never writes boot/GPT/firmware partitions."""
import argparse,ast,hashlib,importlib.util,json,os,pwd,shutil,stat,subprocess,sys,tempfile
from pathlib import Path

PACKAGE_SHA256='4deae51b46894b44f9d7058d0a9345a1f4fec2359909282a3eb08b17a7feaf1c'
VERSION='7.2.6-tb710fu-20261117'
BASE=Path('/var/backups/tb710fu-flash-camera-20261117')
DEST=Path('/opt/tb710fu-flash')/VERSION
UNITS=['tb710fu-macro-camera.service','tb710fu-flash-hardware.service','tb710fu-torch.service']
EXT='tb-torch@tb710fu'
MAP={
 'tb-camera-focus-permission.py':'/usr/local/libexec/tb-camera-focus-permission.py',
 '50-camera-focus-owner.conf':'/etc/systemd/system/tb710fu-camera-focus.service.d/50-camera-focus-owner.conf',
 'tb-macro-hardware.py':'/usr/local/libexec/tb-macro-hardware.py',
 'tb710fu-macro-camera.service':'/etc/systemd/system/tb710fu-macro-camera.service',
 'tb-macro-raw.dtbo':'/lib/firmware/tb-macro-raw.dtbo',
 'tb-camera.py':'/usr/local/bin/tb-rear-camera',
 'tb-front-camera-preview':'/usr/local/bin/tb-front-camera-preview',
 'tb710fu-rear-camera.desktop':'/usr/share/applications/tb710fu-rear-camera.desktop',
 'tb710fu-front-camera.desktop':'/usr/share/applications/tb710fu-front-camera.desktop',
 'tb-torch-daemon.py':'/usr/local/libexec/tb-torch-daemon.py',
 'tb-flash-hardware.py':'/usr/local/libexec/tb-flash-hardware.py',
 'tb-flash-off':'/usr/local/libexec/tb-flash-off',
 'tb710fu-flash-hardware.service':'/etc/systemd/system/tb710fu-flash-hardware.service',
 'tb710fu-torch.service':'/etc/systemd/system/tb710fu-torch.service',
 'org.tb710fu.Torch1.conf':'/etc/dbus-1/system.d/org.tb710fu.Torch1.conf',
 'extension.js':'/usr/share/gnome-shell/extensions/'+EXT+'/extension.js',
 'metadata.json':'/usr/share/gnome-shell/extensions/'+EXT+'/metadata.json',
 'tb-flash.dtbo':'/lib/firmware/tb-flash.dtbo',
}
for name in ['leds-qcom-flash.ko','v4l2-flash-led-class.ko','tb_flash_overlay.ko','tb-flash.dtbo','manifest.json']:
 MAP['local-'+name if name=='tb-flash.dtbo' else name]=str(DEST/name)

for name in ('gc02m1_raw.ko','tb_macro_registration.ko','macro-manifest.json','local-tb-macro-raw.dtbo'):
 MAP[name]=str(Path('/opt/tb710fu-macro')/VERSION/('tb-macro-raw.dtbo' if name=='local-tb-macro-raw.dtbo' else name))


def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def require(ok,message):
 if not ok:raise RuntimeError(message)
def run(*args,check=True):return subprocess.run(args,text=True,capture_output=True,check=check,timeout=70)
def atomic(p,data,mode):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(prefix='.tb-flash-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(name,mode);os.replace(name,p)
 finally:Path(name).unlink(missing_ok=True)
def state_save(s):atomic(BASE/'state.json',(json.dumps(s,indent=2)+'\n').encode(),0o600)
def settings(user,*args):
 account=pwd.getpwnam(user);require(account.pw_uid!=0,'需要普通桌面用户')
 return run('runuser','-u',user,'--','env','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(account.pw_uid)+'/bus','gsettings',*args).stdout.strip()
def extensions(user):return ast.literal_eval(settings(user,'get','org.gnome.shell','enabled-extensions').removeprefix('@as '))
def reload_bus():run('gdbus','call','--system','--dest','org.freedesktop.DBus','--object-path','/org/freedesktop/DBus','--method','org.freedesktop.DBus.ReloadConfig')
def package(source):
 p=source/'package.json';require(sha(p)==PACKAGE_SHA256,'配套清单 SHA256 不匹配；开发源码不能直接安装')
 m=json.loads(p.read_text());require(m['version']==VERSION,'版本不匹配')
 for name,expected in m['files'].items():
  require(Path(name).name==name,'清单路径不安全')
  p=source/name;require(p.is_file() and not p.is_symlink() and sha(p)==expected,'配套校验失败：'+name)
 return m
def hardware_check(source):
 loader=importlib.util.spec_from_file_location('flash_hw',source/'tb-flash-hardware.py');module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
 module.ROOT=source;module.preflight(installed=False)
def original_check(m):
 for name,dest in MAP.items():
  p=Path(dest);require(not p.is_symlink(),'目标不能是符号链接：'+dest)
  if p.exists():
   require(p.is_file() and p.stat().st_uid==0,'目标必须是 root 普通文件：'+dest)
   require(sha(p) in set(m['accepted'].get(dest,[]))|{m['files'][name]},'目标有未知修改，拒绝覆盖：'+dest)

def save_payload(source):
 dest=BASE/('payload-'+PACKAGE_SHA256[:8]+'-'+sha(source/'install-flash-camera.py')[:8])
 if not dest.exists():
  dest.mkdir(mode=0o700)
  m=package(source)
  for name in list(m['files'])+['package.json','install-flash-camera.py']:
   shutil.copyfile(source/name,dest/name);(dest/name).chmod(0o600)
 package(dest)
 require(sha(dest/'install-flash-camera.py')==sha(source/'install-flash-camera.py'),'持久化安装器不一致')
 link=BASE/'.payload-new';link.unlink(missing_ok=True);link.symlink_to(dest.name,target_is_directory=True);os.replace(link,BASE/'payload')

def apply(source,m,user):
 hardware_check(source)
 if (BASE/'state.json').exists():
  s=json.loads((BASE/'state.json').read_text());require(s['user']==user,'桌面用户与现有安装记录不同');require(s['phase'] in ('installed','rolled-back'),'有未完成安装记录，请先 rollback')
  if s['phase']=='installed':
   require(all(Path(p).is_file() and sha(p)==r['after'] for p,r in s['files'].items()),'安装后文件已变更')
  else:
   for dest,r in s['files'].items():
    require(not Path(dest).is_symlink(),'目标变成了符号链接')
    require((Path(dest).is_file() and sha(dest)==r['sha256']) if r['existed'] else not Path(dest).exists(),'回退后文件有未知修改')
    if r['existed']:require(sha(BASE/r['saved'])==r['sha256'],'原始备份已损坏')
  if s['phase']=='installed' and all(dest in s['files'] and s['files'][dest]['after']==m['files'][name] for name,dest in MAP.items()):
   save_payload(source)
   print('已安装，原始快照保留');return
  before_ext=extensions(user)
  for name,dest in MAP.items():
   if dest not in s['files']:
    p=Path(dest);require(not p.is_symlink(),'新增目标不能是符号链接')
    record={'existed':p.exists(),'after':m['files'][name]}
    if p.exists():
     require(p.is_file() and p.stat().st_uid==0 and sha(p) in set(m['accepted'].get(dest,[]))|{m['files'][name]},'新增目标有未知内容：'+dest)
     saved=BASE/('before-added-'+hashlib.sha256(dest.encode()).hexdigest()[:16])
     require(not saved.exists(),'新增目标快照已存在，请检查恢复状态')
     shutil.copyfile(p,saved);saved.chmod(0o600)
     record.update(saved=saved.name,sha256=sha(saved),mode=stat.S_IMODE(p.stat().st_mode))
    s['files'][dest]=record
   record=s['files'][dest]
   record['previous_after']=list(set(record.get('previous_after',[]))|{record['after']})
   record['after']=m['files'][name]
  s['phase']='prepared'
 else:
  original_check(m);BASE.mkdir(mode=0o700,parents=True,exist_ok=True)
  before_ext=extensions(user)
  s={'phase':'prepared','files':{},'user':user,'extension_was_enabled':EXT in before_ext,'services':{u:{'active':run('systemctl','is-active',u,check=False).returncode==0,'enabled':run('systemctl','is-enabled',u,check=False).returncode==0} for u in UNITS}}
  for index,(name,dest) in enumerate(MAP.items()):
   p=Path(dest);record={'existed':p.exists(),'after':m['files'][name]}
   if p.exists():
    saved=BASE/('before-'+str(index));shutil.copyfile(p,saved);saved.chmod(0o600)
    record.update(saved=saved.name,sha256=sha(saved),mode=stat.S_IMODE(p.stat().st_mode))
   s['files'][dest]=record
 for unit in UNITS:
  if unit not in s['services']:s['services'][unit]={'active':run('systemctl','is-active',unit,check=False).returncode==0,'enabled':run('systemctl','is-enabled',unit,check=False).returncode==0}
 focus=Path('/sys/module/tb_focus_manual/parameters/position')
 if 'focus_access' not in s and focus.exists():
  st=focus.stat();s['focus_access']={'uid':st.st_uid,'gid':st.st_gid,'mode':stat.S_IMODE(st.st_mode)}
 state_save(s)
 try:
  save_payload(source)
  for unit in reversed(UNITS):
   result=run('systemctl','stop',unit,check=False)
   require(result.returncode==0 or 'not loaded' in result.stderr,'服务未能安全停止：'+unit)
  for name,dest in MAP.items():
   executable=dest.startswith('/usr/local/')
   atomic(dest,(source/name).read_bytes(),0o755 if executable else 0o644)
  reload_bus();run('systemctl','daemon-reload')
  for unit in UNITS:run('systemctl','enable',unit)
  run('systemctl','start',*UNITS)
  run('python3','/usr/local/libexec/tb-camera-focus-permission.py')
  if EXT not in before_ext:settings(user,'set','org.gnome.shell','enabled-extensions',repr(before_ext+[EXT]))
  s['phase']='installed';state_save(s)
 except BaseException:
  rollback(s);raise
 print('手电筒、主摄自动对焦及前摄/微距相机已安装；快捷设置扩展将在正常桌面会话启用。')

def rollback(s=None):
 require((BASE/'state.json').exists(),'没有本机安装快照')
 s=s or json.loads((BASE/'state.json').read_text())
 for dest,r in s['files'].items():
  p=Path(dest);require(not p.is_symlink(),'目标被其他操作改成了符号链接')
  if r['existed']:require(sha(BASE/r['saved'])==r['sha256'],'备份已损坏：'+dest)
  if p.exists():require(sha(p) in {r.get('sha256'),r['after']}|set(r.get('previous_after',[])),'目标被其他操作修改：'+dest)
 for unit in reversed(UNITS):
  result=run('systemctl','stop',unit,check=False)
  require(result.returncode==0 or 'not loaded' in result.stderr,'服务未能安全停止：'+unit)
 for unit in UNITS:run('systemctl','disable',unit,check=False)
 for dest,r in s['files'].items():
  if r['existed']:atomic(dest,(BASE/r['saved']).read_bytes(),r['mode'])
  else:Path(dest).unlink(missing_ok=True)
 if not s['extension_was_enabled']:
  current=extensions(s['user'])
  if EXT in current:settings(s['user'],'set','org.gnome.shell','enabled-extensions',repr([x for x in current if x!=EXT]))
 focus=Path('/sys/module/tb_focus_manual/parameters/position')
 if os.uname().release==VERSION and focus.exists() and s.get('focus_access'):
  f=s['focus_access'];os.chown(focus,f['uid'],f['gid']);os.chmod(focus,f['mode'])
 reload_bus();run('systemctl','daemon-reload')
 for unit,before in s['services'].items():
  if before['enabled']:run('systemctl','enable',unit)
  if before['active']:run('systemctl','start',unit)
 s['phase']='rolled-back';state_save(s)
 print('已关闭手电筒并恢复原文件、服务及扩展设置，快照保留。')

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['check','apply','rollback'],default='check',nargs='?');parser.add_argument('--user',default='ubuntu');a=parser.parse_args()
 require(os.geteuid()==0,'需要管理员权限');os.umask(0o077)
 source=Path(__file__).resolve().parent;m=package(source)
 if a.action=='rollback':rollback();return
 hardware_check(source)
 if a.action=='check':
  if not (BASE/'state.json').exists():original_check(m)
  print('配套、运行内核、boot、固件和 GPT 校验通过；未写入设备。');return
 apply(source,m,a.user)

if __name__=='__main__':
 try:main()
 except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as exc:raise SystemExit('停止：'+str(exc))
