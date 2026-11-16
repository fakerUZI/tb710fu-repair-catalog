#!/usr/bin/python3
"""Per-user WirePlumber audio rule with protected snapshots and failure recovery."""
import base64
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

SINK = 'alsa_output.platform-sound.pro-output-0'
SOURCE = 'alsa_input.platform-sound.pro-input-2'
RULE = b'''# TB710FU Android playback: keep the speaker clock ahead of the microphone.
monitor.alsa.rules = [
  { matches = [ { node.name = "alsa_input.platform-sound.pro-input-2" } ]
    actions = { update-props = { priority.driver = 1000 } } }
  { matches = [ { node.name = "alsa_output.platform-sound.pro-output-0" } ]
    actions = { update-props = { priority.driver = 2000 api.alsa.period-size = 256 api.alsa.headroom = 256 } } }
]
'''
class Stop(Exception): pass
def require(ok, message):
    if not ok: raise Stop(message)
def digest(data): return hashlib.sha256(data).hexdigest()
def atomic(path, data, mode=0o600):
    require(not path.is_symlink(), '拒绝覆盖符号链接：' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd,tmp = tempfile.mkstemp(prefix='.tb-audio-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(data)
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def snapshot(path):
    require(not path.is_symlink(), '配置不能是符号链接。')
    if not path.exists():return None
    require(path.is_file() and path.stat().st_uid==os.getuid(), '配置必须是当前用户的普通文件。')
    data=path.read_bytes()
    return {'data':base64.b64encode(data).decode(),'sha256':digest(data),'mode':stat.S_IMODE(path.stat().st_mode)}
def unpack(value):
    if value is None:return None
    require(isinstance(value,dict) and set(value)=={'data','sha256','mode'},'原配置快照格式无效。')
    data=base64.b64decode(value['data'],validate=True)
    require(digest(data)==value['sha256'] and type(value['mode']) is int and 0<=value['mode']<=0o777,'原配置快照校验失败。')
    return data

class Repair:
    def __init__(self,config=None,state=None,model=None,release=None):
        home=Path.home()
        self.dest=(Path(os.environ.get('XDG_CONFIG_HOME',home/'.config'))/'wireplumber/wireplumber.conf.d/99-tb710fu-android-audio.conf') if config is None else Path(config)
        self.state=(Path(os.environ.get('XDG_STATE_HOME',home/'.local/state'))/'tb710fu-android-audio') if state is None else Path(state)
        self.record=self.state/'before.json'
        self.model=Path('/proc/device-tree/compatible') if model is None else Path(model)
        self.release=Path('/etc/os-release') if release is None else Path(release)
    def run(self,args,check=True):
        result=subprocess.run(args,capture_output=True,text=True,timeout=30,env=dict(os.environ,LC_ALL='C'))
        require(not check or result.returncode==0,'命令失败：'+' '.join(args)+'\n'+result.stderr[-1000:])
        return result
    def safe(self):
        require(os.geteuid()!=0,'请以当前图形桌面的普通用户运行，不要 sudo。')
        require(not self.state.is_symlink() and not self.record.is_symlink(),'安装记录不能是符号链接。')
        if self.state.exists():require(self.state.is_dir() and self.state.stat().st_uid==os.getuid() and not stat.S_IMODE(self.state.stat().st_mode)&0o077,'安装记录目录必须归当前用户且权限为 700。')
        return snapshot(self.dest)
    def dependencies(self):
        for cmd in ('systemctl','pactl','wireplumber'):require(shutil.which(cmd),'缺少依赖：'+cmd)
    def active(self):return self.run(['systemctl','--user','is-active','--quiet','wireplumber'],False).returncode==0
    def node(self,kind,name):
        result=self.run(['pactl','list','short',kind],False)
        return result.returncode==0 and any(len(parts)>1 and parts[1]==name for parts in (line.split() for line in result.stdout.splitlines()))
    def preflight(self):
        self.safe();self.dependencies()
        require(platform.system()=='Linux' and platform.machine()=='aarch64' and platform.release()=='7.2.6-tb710fu-snap1','仅支持已验证的 ARM64 snap1 内核。')
        require(b'lenovo,tb710fu' in self.model.read_bytes().split(b'\0'),'机型不是 TB710FU。')
        release=dict(line.split('=',1) for line in self.release.read_text().splitlines() if '=' in line)
        require(release.get('ID','').strip('"')=='ubuntu' and release.get('VERSION_ID','').strip('"')=='26.04','仅支持已验证的 Ubuntu 26.04 系列。')
        version=re.search(r'\b(\d+)\.(\d+)\.\d+\b',self.run(['wireplumber','--version']).stdout)
        require(version and version.groups()==('0','5'),'仅支持 WirePlumber 0.5 系列配置。')
        require(self.active(),'当前用户 WirePlumber 未运行。')
        require(self.node('sinks',SINK) and self.node('sources',SOURCE),'未找到已验证的扬声器与麦克风节点。')
    def load(self):
        if not self.record.exists():return None
        data=json.loads(self.record.read_text())
        require('before' in data,'原始配置快照缺失。')
        require(data.get('format')==1 and data.get('target')==str(self.dest),'安装记录版本或目标不匹配。')
        unpack(data.get('before'));unpack(data.get('previous'))
        require(isinstance(data.get('installed_sha256'),str) and re.fullmatch('[a-f0-9]{64}',data['installed_sha256']) and type(data.get('installed_mode')) is int and 0<=data['installed_mode']<=0o777,'安装记录校验值无效。')
        require(data.get('phase') in ('pending','installed'),'安装记录阶段无效。')
        return data
    def save(self,data):
        if data is None:self.record.unlink(missing_ok=True);return
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700);self.state.chmod(0o700)
        atomic(self.record,(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
    def verify(self,record,current,rollback=False):
        actual=(current['sha256'],current['mode']) if current else (None,None)
        allowed=[(record['installed_sha256'],record['installed_mode'])]
        if record['phase']=='pending':
            for before in (record.get('previous'),record.get('before')):allowed.append((before['sha256'],before['mode']) if before else (None,None))
        if rollback and record['before'] is None:allowed.append((None,None))
        require(actual in allowed,'配置在安装后被修改，拒绝覆盖或删除。')
    def restore_config(self,value):
        if value is None:self.dest.unlink(missing_ok=True)
        else:atomic(self.dest,unpack(value),value['mode'])
    def volume(self,required):
        try:
            require(self.node('sinks',SINK),'扬声器未出现。')
            raw=self.run(['pactl','get-sink-volume',SINK]).stdout
            values=[int(v) for v in re.findall(r'[\w-]+:\s+([0-9]+)\s*/',raw)]
            require(values and all(v<=0xffffffff for v in values),'无法解析通道音量。')
            mute=self.run(['pactl','get-sink-mute',SINK]).stdout.strip().split()[-1]
            require(mute in ('yes','no'),'无法解析静音状态。')
            return values,mute
        except (Stop,OSError,IndexError,subprocess.SubprocessError):
            if required:raise Stop('无法保存原音量或静音状态；未修改配置。')
            return None
    def restart(self,volume):
        self.run(['systemctl','--user','restart','wireplumber'])
        if volume is not None:
            deadline=time.monotonic()+20
            while not self.node('sinks',SINK):
                require(time.monotonic()<deadline,'重连后扬声器未出现。');time.sleep(.25)
            values,mute=volume
            self.run(['pactl','set-sink-mute',SINK,'yes'])
            self.run(['pactl','set-sink-volume',SINK,*map(str,values)])
            self.run(['pactl','set-sink-mute',SINK,mute])
        else:
            time.sleep(.25);require(self.active(),'重连后 WirePlumber 未运行。')
    def transaction(self,current,old_record,volume,was_active,operation,commit):
        try:
            if volume is not None:self.run(['pactl','set-sink-mute',SINK,'yes'])
            operation()
            if was_active:self.restart(volume)
            commit()
        except BaseException as error:
            try:
                self.restore_config(current)
                if was_active:self.restart(volume)
                self.save(old_record)
            except BaseException as recovery:
                raise Stop('操作及自动恢复未完成；安装记录保留在 '+str(self.state)+'。请检查音量与配置。\n'+str(recovery)) from error
            raise Stop('操作失败，已恢复本次修改前的配置与音量状态：'+str(error)) from error
    def check(self):
        self.preflight();current=snapshot(self.dest);record=self.load()
        if record:self.verify(record,current)
        elif current:require(current['sha256']==digest(RULE),'已有不同配置，apply 将拒绝覆盖。')
        print('只读检查通过；未修改文件、音量或服务。配置：',self.dest)
    def apply(self):
        self.preflight();current=snapshot(self.dest);old=self.load()
        if old:self.verify(old,current)
        else:require(current is None or current['sha256']==digest(RULE),'已有不同内容的配置，拒绝覆盖。')
        if old and old['phase']=='installed' and current and current['sha256']==digest(RULE):
            print('已安装同一版本；未重复重启音频服务。');return
        volume=self.volume(True)
        data=copy.deepcopy(old) if old else {'format':1,'target':str(self.dest),'before':current}
        data.update(phase='pending',previous=current,installed_sha256=digest(RULE),installed_mode=current['mode'] if current else 0o644)
        self.save(data)
        def install():self.restore_config({'data':base64.b64encode(RULE).decode(),'sha256':digest(RULE),'mode':data['installed_mode']})
        def commit():
            data['phase']='installed';data.pop('previous',None);self.save(data)
        self.transaction(current,old,volume,True,install,commit)
        print('规则已安装，原音量与静音状态已恢复；请测试安卓播放。')
    def rollback(self):
        current=self.safe();old=self.load()
        if old is None:
            require(current is None,'没有本包的安装记录；同名规则可能来自手工配置，不会自动删除。')
            print('本包未安装，无需回退。');return
        self.verify(old,current,rollback=True)
        try:was_active=self.active()
        except (Stop,OSError,subprocess.SubprocessError):was_active=False
        volume=self.volume(False) if was_active else None
        self.transaction(current,old,volume,was_active,lambda:self.restore_config(old['before']),lambda:self.save(None))
        print('已恢复首次安装前的配置；原有同名规则会保留，其他规则未修改。')
        if not was_active or volume is None:print('原音频会话未就绪，未恢复未知音量；请检查桌面音频状态。')
    @contextmanager
    def locked(self):
        self.safe();self.state.mkdir(parents=True,exist_ok=True,mode=0o700);self.state.chmod(0o700)
        lock=self.state/'lock';require(not lock.is_symlink(),'锁文件不能是符号链接。')
        with lock.open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);yield

def main():
    action=sys.argv[1] if len(sys.argv)==2 else 'check';require(action in ('check','apply','rollback'),'动作须为 check、apply 或 rollback。')
    repair=Repair()
    if action=='check':repair.check();return
    def stop(signum,frame):raise Stop('收到终止信号。')
    signal.signal(signal.SIGTERM,stop)
    with repair.locked():(repair.apply if action=='apply' else repair.rollback)()
if __name__=='__main__':
    try:main()
    except (Stop,OSError,ValueError,subprocess.SubprocessError) as exc:print('未完成：',exc,file=sys.stderr);sys.exit(1)
