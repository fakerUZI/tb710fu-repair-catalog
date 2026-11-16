#!/usr/bin/env python3
"""Install the tested per-user Droidloom on-demand launcher, or restore its backup."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

home = Path.home()
source = Path(os.environ['TB710FU_ATTACHMENTS_DIR'])
state = Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')) / 'tb710fu-droidloom-on-demand'
unit = 'droidloom-watch-launchers.service'
files = {
    'launch': home / '.local/bin/droidloom-launch',
    'fix-launchers.py': home / '.local/bin/droidloom-fix-launchers',
    'watch-launchers.py': home / '.local/bin/droidloom-watch-launchers',
    'droidloom-watch-launchers.service': home / '.config/systemd/user' / unit,
}

def ctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def preflight():
    if os.geteuid() == 0:
        raise ValueError('请在桌面会话中以普通用户运行，不要使用 sudo。')
    if any(c.isspace() or c in '"\\`$' for c in str(home)):
        raise ValueError('当前主目录路径含特殊字符，无法安全生成桌面启动项。')
    if not shutil.which('droidloomctl') or not Path('/usr/bin/droidloomctl').exists():
        raise ValueError('未找到 Droidloom；本脚本不安装 Droidloom。')
    if not all(shutil.which(name) for name in ('flock', 'zenity', 'systemctl')):
        raise ValueError('缺少 flock、zenity 或 systemctl。')
    if not os.environ.get('XDG_RUNTIME_DIR') or ctl('show-environment', check=False).returncode:
        raise ValueError('需要已登录的 systemd 用户桌面会话。')
    for source_name, target in files.items():
        if not (source / source_name).is_file() or target.is_symlink():
            raise ValueError('附件缺失或目标是符号链接：' + source_name)
        if target.exists() and not target.is_file():
            raise ValueError('目标不是普通文件：' + str(target))


def apply():
    preflight()
    manifest = state / 'before.json'
    if not manifest.exists():
        state.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(state, 0o700)
        before = {'existing': [], 'enabled': ctl('is-enabled', unit, check=False).returncode == 0,
                  'active': ctl('is-active', unit, check=False).returncode == 0}
        for name, target in files.items():
            if target.exists():
                shutil.copy2(target, state / (name + '.before'))
                before['existing'].append(name)
        manifest.write_text(json.dumps(before), encoding='utf-8')
    for name, target in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        os.chmod(target, 0o644 if name.endswith('.service') else 0o755)
    ctl('daemon-reload')
    ctl('enable', '--now', unit)
    subprocess.run([str(files['fix-launchers.py'])], check=True)
    print('已启用安卓应用按需启动；不会启用安卓容器开机启动。')
    print('保留的原文件位于：', state)


def rollback():
    preflight()
    manifest = state / 'before.json'
    if not manifest.is_file():
        raise ValueError('没有找到本脚本保存的回退记录。')
    before = json.loads(manifest.read_text(encoding='utf-8'))
    for name, target in files.items():
        if not target.is_file() or target.read_bytes() != (source / name).read_bytes():
            raise ValueError('配置已被修改，拒绝覆盖：' + str(target))
    ctl('disable', '--now', unit)
    for name, target in files.items():
        if name in before['existing']:
            shutil.copy2(state / (name + '.before'), target)
        else:
            target.unlink()
    ctl('daemon-reload')
    if before['enabled']:
        ctl('enable', unit)
    if before['active']:
        ctl('start', unit)
    if 'launch' not in before['existing']:
        prefix = 'Exec=' + str(files['launch']) + ' '
        for target in (home / '.local/share/applications').glob('droidloom-*.desktop'):
            if target.is_file() and not target.is_symlink():
                data = target.read_text(encoding='utf-8')
                if 'X-Droidloom-Managed=true\n' in data and prefix in data:
                    target.write_text(data.replace(prefix, 'Exec=/usr/bin/droidloomctl launch ', 1), encoding='utf-8')
    shutil.rmtree(state)
    print('已恢复原有用户配置；安卓数据未删除。')

try:
    action = sys.argv[1] if len(sys.argv) > 1 else 'apply'
    if action == 'check':
        preflight(); print('预检通过。')
    elif action == 'apply':
        apply()
    elif action == 'rollback':
        rollback()
    else:
        raise ValueError('操作须为 check、apply 或 rollback。')
except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
    print('修复未完成：', exc, file=sys.stderr)
    sys.exit(1)
