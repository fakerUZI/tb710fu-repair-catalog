#!/usr/bin/python3
"""Per-user power-key installer; complete original snapshots survive reapply."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

UNIT = 'tb-gnome-power-button.service'
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def atomic(path, data, mode=0o600):
    if path.is_symlink(): raise ValueError('拒绝覆盖符号链接：' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.tb-power-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
        os.chmod(name, mode); os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

class Installer:
    def __init__(self, home=None, source=None, state=None, hardware=None):
        self.home = Path.home() if home is None else Path(home)
        self.source = Path(os.environ.get('TB710FU_ATTACHMENTS_DIR', Path(__file__).parent)) if source is None else Path(source)
        self.state = Path(os.environ.get('XDG_STATE_HOME', self.home / '.local/state')) / 'tb710fu-power-key' if state is None else Path(state)
        self.hardware = Path('/sys/class') if hardware is None else Path(hardware)
        self.files = {
            'tb-power-key.py': (self.home / '.local/bin/tb-power-key.py', 0o755),
            'tb-power-key-off': (self.home / '.local/bin/tb-power-key-off', 0o755),
            UNIT: (self.home / '.config/systemd/user' / UNIT, 0o644),
            'install-power-key.py': (self.home / '.local/lib/tb710fu-power-key/install-power-key.py', 0o755)}
        self.manifest = self.state / 'before.json'
    def ctl(self, *args, check=True):
        return subprocess.run(['systemctl', '--user', *args], check=check, text=True, capture_output=True, timeout=30)
    def targets_safe(self):
        if self.state.is_symlink() or self.manifest.is_symlink(): raise ValueError('回退记录不能是符号链接。')
        for target, _ in self.files.values():
            if target.is_symlink() or (target.exists() and not target.is_file()): raise ValueError('目标不是普通文件：' + str(target))
    def preflight(self):
        if os.geteuid() == 0: raise ValueError('请以普通桌面用户运行，不要 sudo。')
        self.targets_safe()
        if not list(self.hardware.glob('drm/*-DSI-1/dpms')): raise ValueError('找不到 DSI dpms 节点，本机不具备前提；没有修改任何文件。')
        for name in self.files:
            if not (self.source / name).is_file(): raise ValueError('缺少附件：' + name)
        self.ctl('show-environment')
    def report(self):
        self.preflight(); print('只读检查：未修改文件或服务。')
        for path in sorted(self.hardware.glob('drm/*-DSI-1/dpms')): print('dpms:', path.read_text().strip(), path)
        for path in sorted(self.hardware.glob('backlight/*/bl_power')): print('bl_power:', path.read_text().strip(), path)
        for name, (target, _) in self.files.items(): print(name, '已安装且与包一致' if target.is_file() and digest(target) == digest(self.source / name) else '未安装或内容不同')
        print('FragmentPath:', self.ctl('show', '-p', 'FragmentPath', '--value', UNIT, check=False).stdout.strip())
        print('状态:', self.ctl('is-active', UNIT, check=False).stdout.strip())
    def save(self, data): atomic(self.manifest, (json.dumps(data, indent=2) + '\n').encode())
    def load(self):
        data = json.loads(self.manifest.read_text())
        if data.get('format') != 1 or set(data.get('files', {})) != set(self.files): raise ValueError('回退记录格式无效，拒绝猜测旧 .bak 文件。')
        return data
    def verify(self, data):
        self.targets_safe()
        for name, (target, mode) in self.files.items():
            saved = data['files'][name]; allowed = [(saved['installed_sha256'], mode)]
            if saved['existed']:
                backup = self.state / (name + '.before')
                if backup.is_symlink() or digest(backup) != saved['before_sha256']: raise ValueError('原始备份校验失败：' + name)
                if data.get('phase') in ('applying', 'restoring'): allowed.append((saved['before_sha256'], saved['mode']))
            elif data.get('phase') in ('applying', 'restoring'): allowed.append((None, None))
            if data.get('phase') in ('applying', 'restoring') and saved.get('previous_installed_sha256'): allowed.append((saved['previous_installed_sha256'], mode))
            actual = (digest(target), stat.S_IMODE(target.stat().st_mode)) if target.is_file() else (None, None)
            if actual not in allowed: raise ValueError('文件在安装后被修改，拒绝覆盖：' + str(target))
    def apply(self):
        self.preflight()
        if self.manifest.exists(): data = self.load(); self.verify(data)
        else:
            self.state.mkdir(parents=True, mode=0o700, exist_ok=True); self.state.chmod(0o700)
            enabled = self.ctl('is-enabled', UNIT, check=False).stdout.strip()
            data = {'format': 1, 'files': {}, 'enabled': enabled if enabled in ('enabled', 'enabled-runtime') else '', 'active': self.ctl('is-active', UNIT, check=False).returncode == 0}
            for name, (target, _) in self.files.items():
                record = {'existed': target.exists()}
                if target.exists():
                    record.update(before_sha256=digest(target), mode=stat.S_IMODE(target.stat().st_mode)); atomic(self.state / (name + '.before'), target.read_bytes(), record['mode'])
                data['files'][name] = record
        data['phase'] = 'applying'
        for name in self.files:
            saved = data['files'][name]
            if 'installed_sha256' in saved: saved['previous_installed_sha256'] = saved['installed_sha256']
            saved['installed_sha256'] = digest(self.source / name)
        self.save(data)
        for name, (target, mode) in self.files.items(): atomic(target, (self.source / name).read_bytes(), mode)
        self.ctl('daemon-reload'); self.ctl('enable', UNIT); self.ctl('restart', UNIT); self.ctl('is-active', UNIT)
        fragment = self.ctl('show', '-p', 'FragmentPath', '--value', UNIT).stdout.strip()
        if not fragment or Path(fragment).resolve() != self.files[UNIT][0].resolve(): raise ValueError('服务未加载用户级覆盖；快照已保留，请检查后回退。')
        data['phase'] = 'installed'; self.save(data)
        print('安装完成。短按切换屏幕，长按显示关机菜单；无需重启。')
        print('回退：TB710FU_FIX_ACTION=rollback，或 tb-power-key-off。')
    def rollback(self):
        if os.geteuid() == 0: raise ValueError('请以普通桌面用户运行。')
        self.targets_safe()
        if not self.manifest.is_file(): raise ValueError('没有本版安装记录；不会猜测或删除旧版配置。')
        data = self.load(); self.verify(data)
        self.ctl('stop', UNIT); self.ctl('disable', UNIT)
        data['phase'] = 'restoring'; self.save(data)
        for name, (target, _) in self.files.items():
            saved = data['files'][name]
            if saved['existed']: atomic(target, (self.state / (name + '.before')).read_bytes(), saved['mode'])
            elif target.exists(): target.unlink()
        self.ctl('daemon-reload')
        if data['enabled']: self.ctl('enable', *(['--runtime'] if data['enabled'] == 'enabled-runtime' else []), UNIT)
        if data['active']: self.ctl('start', UNIT)
        shutil.rmtree(self.state)
        print('已恢复首次安装前的文件与服务启用/运行状态；原有用户自定义覆盖也会恢复。')

def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'apply'
    if action not in ('check', 'apply', 'rollback'): raise ValueError('操作须为 check、apply 或 rollback。')
    installer = Installer(); {'check': installer.report, 'apply': installer.apply, 'rollback': installer.rollback}[action]()
if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc: print('未完成：', exc, file=sys.stderr); sys.exit(1)
