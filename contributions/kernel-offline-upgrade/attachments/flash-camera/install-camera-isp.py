#!/usr/bin/python3
"""Offline libcamera gain fix, with exact stock packages and timed rollback."""
import argparse,hashlib,importlib.util,json,os,subprocess,sys
from pathlib import Path
SOURCE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('addon',SOURCE/'install-flash-camera.py');addon=importlib.util.module_from_spec(spec);spec.loader.exec_module(addon)
BASE=Path('/var/backups/tb710fu-camera-isp-20261117')
OLD='0.7.0-1ubuntu2';NEW=OLD+'+tb710fu20261117.3'
NAMES=('libcamera0.7','libcamera-ipa','libcamera-tools','libcamera-v4l2','gstreamer1.0-libcamera')
TIMER='tb710fu-camera-isp-autorevert'
def run(*args,timeout=90,check=True):return subprocess.run(args,check=check,text=True,capture_output=True,timeout=timeout)
def versions():
 return {n:run('dpkg-query','-W','-f=${Version}',n).stdout for n in NAMES}
def files(version):return [SOURCE/(n+'_'+version+'_arm64.deb') for n in NAMES]
def check():
 addon.package(SOURCE);addon.hardware_check(SOURCE)
 v=versions();addon.require(all(x in (OLD,NEW) for x in v.values()),'libcamera 存在未知版本，拒绝覆盖')
 addon.require(not run('dpkg','--audit').stdout.strip(),'dpkg 有未完成事务')
 verified=run('dpkg','--verify',*NAMES,check=False)
 addon.require(verified.returncode==0 and not verified.stdout.strip(),'libcamera 文件有本地修改，拒绝覆盖')
 held=set(run('apt-mark','showhold').stdout.split());addon.require(not held.intersection(NAMES),'libcamera 被锁定，拒绝覆盖')
 for version in (OLD,NEW):
  for n,p in zip(NAMES,files(version)):
   addon.require(run('dpkg-deb','-f',str(p),'Package').stdout.strip()==n,'deb 包名不匹配')
   addon.require(run('dpkg-deb','-f',str(p),'Version').stdout.strip()==version,'deb 版本不匹配')
   addon.require(run('dpkg-deb','-f',str(p),'Architecture').stdout.strip()=='arm64','deb 架构不匹配')
def state_save(s):addon.atomic(BASE/'state.json',(json.dumps(s,indent=2)+'\n').encode(),0o600)
def cancel_timer():run('systemctl','stop',TIMER+'.timer',check=False)
def rollback():
 addon.package(SOURCE)
 s=json.loads((BASE/'state.json').read_text())
 addon.require(s['old_version']==OLD,'恢复记录不匹配')
 # Even a partly configured version after an interrupted dpkg is repaired here.
 addon.require(all(v in (OLD,NEW) for v in versions().values()),'libcamera 被其他升级替换，拒绝回退')
 run('dpkg','--install',*map(str,files(OLD)),timeout=180)
 addon.require(all(v==OLD for v in versions().values()),'软件包恢复未完成')
 cancel_timer();s['phase']='rolled-back';state_save(s)
 print('libcamera 已恢复原 Ubuntu 版本；内核和相机驱动未改变。')
def smoke():
 r=run('gst-inspect-1.0','libcamerasrc');addon.require('libcamerasrc' in r.stdout,'GStreamer 插件不可用')
 for path in ('/base/soc@0/cci@ac15000/i2c-bus@1/camera@36','/base/soc@0/cci@ac15000/i2c-bus@0/camera@39'):
  r=run('timeout','-k','3','18','cam','--camera',path,'--stream','role=viewfinder,width=640,height=480','--capture=30','--metadata',timeout=25)
  addon.require('seq: 000029' in r.stdout,'相机采集不足 30 帧')
  addon.require('Failed to create camera sensor helper' not in r.stderr,'传感器增益适配未加载')
def apply():
 check();BASE.mkdir(mode=0o700,parents=True,exist_ok=True)
 if (BASE/'state.json').exists():
  s=json.loads((BASE/'state.json').read_text());addon.require(s['phase'] in ('complete','rolled-back'),'有未完成软件包安装，请先 rollback')
  if s['phase']=='complete' and all(v==NEW for v in versions().values()):smoke();print('增益修复已安装并验证');return
 else:
  addon.require(all(v==OLD for v in versions().values()),'初次安装必须从原 Ubuntu libcamera 开始')
  s={'old_version':OLD,'new_version':NEW,'phase':'prepared'}
 s['phase']='prepared';state_save(s);addon.save_payload(SOURCE)
 persistent=addon.BASE/'payload'/'install-camera-isp.py'
 cancel_timer()
 run('systemd-run','--collect','--unit='+TIMER,'--on-active=5m','--timer-property=AccuracySec=1s','python3',str(persistent),'rollback')
 try:
  run('dpkg','--install',*map(str,files(NEW)),timeout=180)
  addon.require(all(v==NEW for v in versions().values()),'新软件包安装不完整')
  smoke()
  cancel_timer();s['phase']='complete';state_save(s)
 except BaseException:
  rollback();raise
 print('libcamera 增益换算与曝光阈值修复已安装，前后摄各 30 帧验证通过。')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['check','apply','rollback'],default='check',nargs='?');a=p.parse_args()
 addon.require(os.geteuid()==0,'需要管理员权限');os.umask(0o077)
 {'check':check,'apply':apply,'rollback':rollback}[a.action]()
if __name__=='__main__':
 try:main()
 except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as e:raise SystemExit('停止：'+str(e))
