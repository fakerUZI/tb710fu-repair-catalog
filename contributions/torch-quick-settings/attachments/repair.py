"""Restore the TB710FU GNOME Quick Settings torch tile."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile

UUID = 'tb-torch@tb710fu'
EXT = Path('/usr/share/gnome-shell/extensions') / UUID
STATE = Path('/var/lib/tb710fu-torch-quick-settings')
KEYS = ('enabled-extensions', 'disabled-extensions')
HASHES = {
    'extension.js': '7a5ed13170542a1e64e6f81be3f6009861d188e3f046e7ad4b8f2d9024f1bfd1',
    'metadata.json': 'c089bf83605a2844e7b11987be03075d412b3ab806cfad7363f60025c53f7d91',
}


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True, timeout=20).stdout.strip()


def desktop_uid():
    if os.geteuid() != 0:
        raise ValueError('请由修复助手申请管理员权限，或在 GNOME 终端用 sudo 执行。')
    raw = os.environ.get('PKEXEC_UID') or os.environ.get('SUDO_UID') or ''
    if not raw.isdecimal() or not (1000 <= int(raw) < 60000):
        raise ValueError('无法识别发起操作的普通桌面用户。')
    uid = int(raw)
    matches = []
    for line in run('/usr/bin/loginctl', 'list-sessions', '--no-legend', '--no-pager').splitlines():
        columns = line.split()
        if len(columns) < 2 or columns[1] != str(uid):
            continue
        result = run('/usr/bin/loginctl', 'show-session', columns[0],
                     '-p', 'Active', '-p', 'Remote', '-p', 'Type', '-p', 'Class')
        properties = dict(item.split('=', 1) for item in result.splitlines())
        if properties == {'Active': 'yes', 'Remote': 'no', 'Type': 'wayland', 'Class': 'user'}:
            matches.append(columns[0])
    if len(matches) != 1 or not Path('/run/user', str(uid), 'bus').is_socket():
        raise ValueError('请在该用户当前登录的本地图形桌面运行。')
    return uid


def settings_command(uid, *args):
    user = pwd.getpwuid(uid).pw_name
    runtime = f'/run/user/{uid}'
    return run('/usr/sbin/runuser', '-u', user, '--', '/usr/bin/env',
               'XDG_RUNTIME_DIR=' + runtime,
               'DBUS_SESSION_BUS_ADDRESS=unix:path=' + runtime + '/bus',
               '/usr/bin/gsettings', args[0], 'org.gnome.shell', *args[1:])


def read_selection(uid):
    result = {}
    for key in KEYS:
        if settings_command(uid, 'writable', key) != 'true':
            raise ValueError('GNOME 扩展设置不可写：' + key)
        values = ast.literal_eval(settings_command(uid, 'get', key).removeprefix('@as '))
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError('GNOME 扩展列表格式异常：' + key)
        result[key] = values
    return result


def write_selection(uid, original, desired):
    changed = []
    try:
        for key in KEYS:
            if desired[key] != original[key]:
                settings_command(uid, 'set', key, repr(desired[key]))
                changed.append(key)
    except (ValueError, subprocess.SubprocessError):
        for key in reversed(changed):
            settings_command(uid, 'set', key, repr(original[key]))
        raise


def snapshot_path(uid):
    if STATE.is_symlink():
        raise ValueError('手电筒回退目录不能是符号链接。')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.stat().st_uid != 0 or stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise ValueError('手电筒回退目录权限异常。')
    target = STATE / f'{uid}.json'
    if target.is_symlink():
        raise ValueError('手电筒回退记录不能是符号链接。')
    return target


def save_record(path, selection):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({key: selection[key].index(UUID) if UUID in selection[key] else None
                       for key in KEYS}, stream)
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def apply(uid, original, directory):
    enabled = list(original['enabled-extensions'])
    if UUID not in enabled:
        enabled.append(UUID)
    desired = {'enabled-extensions': enabled,
               'disabled-extensions': [value for value in original['disabled-extensions'] if value != UUID]}
    if original == desired and stat.S_IMODE(directory.stat().st_mode) == 0o755:
        return
    backup = snapshot_path(uid)
    if not backup.exists():
        save_record(backup, original)
    directory.chmod(0o755)
    write_selection(uid, original, desired)


def rollback(uid, original):
    backup = snapshot_path(uid)
    old = json.loads(backup.read_text())
    if set(old) != set(KEYS) or any(value is not None and (type(value) is not int or value < 0) for value in old.values()):
        raise ValueError('手电筒回退记录格式异常。')
    desired = {key: [value for value in original[key] if value != UUID] for key in KEYS}
    for key, index in old.items():
        if index is not None:
            desired[key].insert(index, UUID)
    write_selection(uid, original, desired)
    backup.unlink()


def verify_device():
    compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    if b'lenovo,tb710fu' not in compatible:
        raise ValueError('只支持 Lenovo TB710FU。')
    version = run('/usr/bin/gnome-shell', '--version')
    if not (version == 'GNOME Shell 50' or version.startswith('GNOME Shell 50.')):
        raise ValueError('此磁贴适用于 GNOME Shell 50。')
    if EXT.is_symlink() or not EXT.is_dir() or EXT.stat().st_uid != 0:
        raise ValueError('系统手电筒扩展目录异常。')
    for name, checksum in HASHES.items():
        path = EXT/name
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
            raise ValueError('扩展文件路径异常：' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('扩展文件与已审核镜像版本不一致：' + name)


def verify_backend():
    print('扩展目录权限：' + oct(stat.S_IMODE(EXT.stat().st_mode)), flush=True)
    for service in ('tb710fu-flash-hardware.service', 'tb710fu-torch.service'):
        try:
            run('/usr/bin/systemctl', 'is-active', '--quiet', service)
        except subprocess.CalledProcessError as error:
            raise ValueError('后台服务未运行：' + service) from error
    run('/usr/bin/gdbus', 'call', '--system', '--dest', 'org.tb710fu.Torch1', '--object-path',
        '/org/tb710fu/Torch1', '--method', 'org.tb710fu.Torch1.GetState')
    print('闪光灯驱动和手电筒服务可读。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='check', choices=('check', 'apply', 'rollback'))
    action = parser.parse_args().action
    uid = desktop_uid()
    original = read_selection(uid)
    if action == 'rollback':
        rollback(uid, original)
        print('已恢复此用户的磁贴选择。请注销并重新登录。')
        return
    verify_device()
    if settings_command(uid, 'get', 'disable-user-extensions') != 'false':
        raise ValueError('请先在 GNOME 启用用户扩展。')
    verify_backend()
    if action == 'apply':
        apply(uid, original, EXT)
        print('手电筒入口已启用。请保存工作，注销并重新登录。')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('手电筒入口修复停止：' + str(error), file=sys.stderr)
        sys.exit(1)
