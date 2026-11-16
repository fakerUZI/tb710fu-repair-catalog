#!/usr/bin/env python3
"""Per-user Droidloom launcher installation with protected, stable rollback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

home = Path.home()
source = Path(os.environ.get('TB710FU_ATTACHMENTS_DIR', Path(__file__).parent))
state = Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')) / 'tb710fu-droidloom-on-demand'
unit = 'droidloom-watch-launchers.service'
DROIDLOOM = Path('/usr/bin/droidloomctl')
files = {
    'launch': home / '.local/bin/droidloom-launch',
    'fix-launchers.py': home / '.local/bin/droidloom-fix-launchers',
    'watch-launchers.py': home / '.local/bin/droidloom-watch-launchers',
    'droidloom-watch-launchers.service': home / '.config/systemd/user' / unit}
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def ctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, capture_output=True, text=True, timeout=30)
def atomic(path, data, mode=0o600):
    if path.is_symlink(): raise ValueError('目标不能是符号链接：' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.droidloom-', dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(data)
        os.chmod(tmp,mode); os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def save(before): atomic(state/'before.json', (json.dumps(before,indent=2)+'\n').encode())
def preflight(runtime=True):
    if os.geteuid()==0: raise ValueError('请在桌面会话中以普通用户运行，不要 sudo。')
    if any(c.isspace() or c in "\"'\\`$><~|&;*?#()%" for c in str(home)): raise ValueError('主目录路径含特殊字符，无法安全生成桌面启动项。')
    if state.is_symlink() or (state/'before.json').is_symlink(): raise ValueError('回退记录不能是符号链接。')
    if not os.environ.get('XDG_RUNTIME_DIR') or ctl('show-environment',check=False).returncode: raise ValueError('需要已登录的 systemd 用户桌面会话。')
    if runtime and (not DROIDLOOM.exists() or not all(shutil.which(x) for x in ('droidloomctl','flock','zenity','systemctl'))): raise ValueError('缺少 Droidloom、flock、zenity 或 systemctl。')
    for name,target in files.items():
        if runtime and not (source/name).is_file(): raise ValueError('附件缺失：'+name)
        if target.is_symlink() or (target.exists() and not target.is_file()): raise ValueError('目标不是普通文件：'+str(target))
def load():
    before=json.loads((state/'before.json').read_text())
    if before.get('format')!=1 or set(before.get('installed_sha256',{}))!=set(files) or not set(before.get('existing',[])).issubset(files): raise ValueError('回退记录格式不支持，请先核对原备份。')
    return before
def verify(before):
    for name,target in files.items():
        expected=[(before['installed_sha256'][name],0o644 if name.endswith('.service') else 0o755)]
        if name in before['existing']:
            backup=state/(name+'.before')
            if backup.is_symlink() or sha(backup)!=before['before_sha256'][name]: raise ValueError('原文件备份校验失败：'+name)
            if before.get('phase') in ('applying','restoring'): expected.append((before['before_sha256'][name],before['modes'][name]))
        elif before.get('phase') in ('applying','restoring'): expected.append((None,None))
        if before.get('phase') in ('applying','restoring') and before.get('previous_sha256',{}).get(name): expected.append((before['previous_sha256'][name],0o644 if name.endswith('.service') else 0o755))
        actual=(sha(target),stat.S_IMODE(target.stat().st_mode)) if target.is_file() else (None,None)
        if actual not in expected: raise ValueError('配置已被修改，拒绝覆盖：'+str(target))
def apply():
    preflight()
    if (state/'before.json').exists(): before=load(); verify(before)
    else:
        state.mkdir(parents=True,mode=0o700,exist_ok=True);state.chmod(0o700)
        enabled=ctl('is-enabled',unit,check=False).stdout.strip()
        before={'format':1,'existing':[],'enabled':enabled if enabled in ('enabled','enabled-runtime') else '', 'active':ctl('is-active',unit,check=False).returncode==0,'before_sha256':{},'modes':{}}
        for name,target in files.items():
            if target.exists():
                before['existing'].append(name); before['before_sha256'][name]=sha(target); before['modes'][name]=stat.S_IMODE(target.stat().st_mode)
                atomic(state/(name+'.before'),target.read_bytes(),before['modes'][name])
    before['previous_sha256']=before.get('installed_sha256',{});before['installed_sha256']={name:sha(source/name) for name in files};before['phase']='applying';save(before)
    for name,target in files.items(): atomic(target,(source/name).read_bytes(),0o644 if name.endswith('.service') else 0o755)
    ctl('daemon-reload');ctl('enable',unit);ctl('restart',unit)
    subprocess.run([str(files['fix-launchers.py'])],check=True,timeout=30)
    before['phase']='installed';save(before)
    print('已启用安卓应用按需启动；不会启用安卓容器开机启动。原始快照保留在：',state)
def rollback():
    preflight(runtime=False)
    if not (state/'before.json').is_file(): raise ValueError('没有找到本版回退记录。')
    before=load();verify(before)
    ctl('disable','--now',unit);before['phase']='restoring';save(before)
    for name,target in files.items():
        if name in before['existing']: atomic(target,(state/(name+'.before')).read_bytes(),before['modes'][name])
        elif target.exists(): target.unlink()
    ctl('daemon-reload')
    if before['enabled']: ctl('enable',*(['--runtime'] if before['enabled']=='enabled-runtime' else []),unit)
    if before['active']: ctl('start',unit)
    if 'launch' not in before['existing']:
        prefix='Exec='+str(files['launch'])+' '
        for target in (home/'.local/share/applications').glob('droidloom-*.desktop'):
            if target.is_file() and not target.is_symlink():
                data=target.read_text(encoding='utf-8')
                if 'X-Droidloom-Managed=true\n' in data and prefix in data:
                    atomic(target,data.replace(prefix,'Exec=/usr/bin/droidloomctl launch ',1).encode(),stat.S_IMODE(target.stat().st_mode))
    shutil.rmtree(state);print('已恢复原有用户文件与服务状态；安卓数据未删除。')
def main():
    action=sys.argv[1] if len(sys.argv)>1 else 'apply'
    if action=='check': preflight();print('只读预检通过；未修改文件或服务。')
    elif action=='apply': apply()
    elif action=='rollback': rollback()
    else: raise ValueError('操作须为 check、apply 或 rollback。')
if __name__=='__main__':
    try: main()
    except (OSError,ValueError,subprocess.SubprocessError) as exc: print('修复未完成：',exc,file=sys.stderr);sys.exit(1)
