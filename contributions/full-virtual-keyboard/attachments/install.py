"""Install the complete TB710FU virtual keyboard."""
import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from zipfile import ZipFile

SOURCE = Path(__file__).resolve().parent
UUIDS = (
    'tb-osk-input-policy@tb710fu', 'tb-osk-shortcuts@tb710fu',
    'tb-osk-toggle@tb710fu', 'kimpanel@kde.org', 'tb-wowtype-osk@tb710fu',
    'wowtype-theme@gloridust.github.io', 'tb-backspace-fallback@tb710fu',
)
MANAGED = set(UUIDS)
KEYS = ('enabled-extensions', 'disabled-extensions')
SESSION_FILES = ('session/tb-fcitx5-session.py', 'session/20-display-session.conf')
# Fcitx config and GNOME settings helpers follow the existing repair entries.
def preedit_config(text):
    """Change just Behavior/PreeditEnabledByDefault, preserving other settings."""
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    sections = [(i, m.group(1)) for i, line in enumerate(lines)
                for m in [re.match(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$', line.rstrip('\r\n'))] if m]
    positions = [i for i, name in sections if name == 'Behavior']
    if len(positions) > 1:
        raise ValueError('Duplicate Behavior sections; inspect Fcitx config manually.')
    if not positions:
        prefix = text + (newline if text and not text.endswith(('\n', '\r')) else '')
        return prefix + '[Behavior]' + newline + 'PreeditEnabledByDefault=False' + newline
    start = positions[0]
    end = next((i for i, _ in sections if i > start), len(lines))
    keys = [i for i in range(start+1, end)
            if re.match(r'^\s*PreeditEnabledByDefault\s*=', lines[i])]
    if len(keys) > 1:
        raise ValueError('Duplicate preedit keys; inspect Fcitx config manually.')
    if keys:
        i = keys[0]
        m = re.match(r'^(\s*PreeditEnabledByDefault\s*=\s*)[^#;\r\n]*(.*)$', lines[i].rstrip('\r\n'))
        ending = newline if lines[i].endswith(('\n', '\r')) else ''
        lines[i] = m.group(1) + 'False' + m.group(2) + ending
    else:
        if end and not lines[end-1].endswith(('\n', '\r')):
            lines[end-1] += newline
        lines.insert(end, 'PreeditEnabledByDefault=False' + newline)
    return ''.join(lines)


def command(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=30).stdout


def settings(key):
    value = command("gsettings", "get", "org.gnome.shell", key).strip().removeprefix("@as ")
    result = ast.literal_eval(value)
    if not isinstance(result, list) or not all(isinstance(item, str) for item in result):
        raise ValueError("GNOME 扩展设置格式未知")
    return result


def set_settings(key, value):
    if value != settings(key):
        command("gsettings", "set", "org.gnome.shell", key, repr(value))
        if settings(key) != value:
            raise RuntimeError(f"GNOME 设置写入未生效：{key}")


def preflight(installing):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('请在平板的 GNOME 桌面以普通用户运行。')
    osinfo = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    model = Path('/sys/firmware/devicetree/base/model').read_bytes()
    if (b'TB710FU' not in model or os.uname().machine != 'aarch64'
            or osinfo.get('ID', '').strip('"') != 'ubuntu'
            or osinfo.get('VERSION_ID', '').strip('"') != '26.04'):
        raise ValueError('此条目适用于 TB710FU 的 Ubuntu 26.04 ARM64。')
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    display = os.environ.get('WAYLAND_DISPLAY')
    if (os.environ.get('XDG_SESSION_TYPE') != 'wayland' or not os.environ.get('DBUS_SESSION_BUS_ADDRESS')
            or not runtime or not display or not (Path(runtime)/display).is_socket()):
        raise ValueError('请从已登录的 GNOME Wayland 桌面运行。')
    if not re.fullmatch(r'GNOME Shell 50(?:\.\d+)*', command('gnome-shell', '--version').strip()):
        raise ValueError('需要 GNOME Shell 50。')
    for key in KEYS:
        if command('gsettings', 'writable', 'org.gnome.shell', key).strip() != 'true':
            raise ValueError(f'GNOME 扩展选择不可写：{key}')
    if installing:
        if command('gsettings', 'get', 'org.gnome.shell', 'disable-user-extensions').strip() != 'false':
            raise ValueError('请先启用 GNOME 用户扩展。')
        if not all(shutil.which(tool) for tool in ('fcitx5', 'glib-compile-schemas')):
            raise ValueError('需要已安装的 Fcitx5 和 glib-compile-schemas。')
        if not Path('/usr/share/fcitx5/addon/wowtype.conf').is_file():
            raise ValueError('需要已安装的 WowType Fcitx5 插件。')
        command('systemctl', '--user', 'cat', 'tb-fcitx5.service')
        config_root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
        profile = config_root/'fcitx5/profile'
        if not profile.is_file() or not re.search(r'^Name=wowtype\s*$', profile.read_text(), re.MULTILINE):
            raise ValueError('请先在 Fcitx5 中添加 WowType 输入法。')


def stage_bundle(directory):
    with ZipFile(SOURCE/'payload.zip') as bundle:
        entries = bundle.infolist()
        if (len(entries) > 100 or sum(info.file_size for info in entries) > 2 * 1024 * 1024
                or len({info.filename for info in entries}) != len(entries)):
            raise ValueError('附件内容异常。')
        manifest = json.loads(bundle.read('manifest.json'))
        if set(bundle.namelist()) != set(manifest) | {'manifest.json'}:
            raise ValueError('附件文件清单不匹配。')
        for entry in entries:
            name = entry.filename
            if name == 'manifest.json':
                continue
            path = PurePosixPath(name)
            if (path.as_posix() != name or '\\' in name or '..' in path.parts or
                    stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)):
                raise ValueError(f'附件路径异常：{name}')
            pieces = path.parts
            if not (
                name in SESSION_FILES
                or (len(pieces) == 2 and pieces[0] == 'licenses')
                or (len(pieces) == 6 and pieces[:3] == ('extensions', 'kimpanel@kde.org', 'locale')
                    and re.fullmatch(r'[a-zA-Z_]+', pieces[3])
                    and pieces[4:] == ('LC_MESSAGES', 'gnome-shell-extensions-kimpanel.mo'))
                or (len(pieces) in (3, 4) and pieces[0] == 'extensions' and pieces[1] in UUIDS
                    and (len(pieces) == 3 or pieces[2] == 'schemas')
                    and path.suffix in ('.js', '.json', '.css', '.xml'))
            ):
                raise ValueError(f'附件不在安装清单范围内：{name}')
            data = bundle.read(entry)
            if hashlib.sha256(data).hexdigest() != manifest[name]:
                raise ValueError(f'附件校验失败：{name}')
            output = directory.joinpath(*pieces)
            output.parent.mkdir(exist_ok=True, parents=True)
            output.write_bytes(data)
    for uuid in UUIDS:
        extension = directory/'extensions'/uuid
        metadata = json.loads((extension/'metadata.json').read_text())
        if metadata['uuid'] != uuid or '50' not in metadata['shell-version']:
            raise ValueError(f'扩展标识或版本不符：{uuid}')
        if not (extension/'extension.js').is_file():
            raise ValueError(f'缺少扩展入口：{uuid}')
        if (extension/'schemas').is_dir():
            command('glib-compile-schemas', '--strict', str(extension/'schemas'))
    (directory/SESSION_FILES[0]).chmod(0o755)


def targets():
    home = Path.home()
    data = Path(os.environ.get('XDG_DATA_HOME') or home/'.local/share')
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home/'.config')
    state = Path(os.environ.get('XDG_STATE_HOME') or home/'.local/state')
    return (
        [data/'gnome-shell/extensions'/uuid for uuid in UUIDS] + [
            home/'.local/libexec/tb-fcitx5-session.py',
            config/'systemd/user/tb-fcitx5.service.d/20-display-session.conf',
            config/'fcitx5/config',
        ],
        state/'tb710fu-virtual-keyboard',
    )


def validate_targets(paths):
    for path in paths:
        if path.is_symlink() or (path.exists() and not (path.is_dir() or path.is_file())):
            raise ValueError(f'用户设置或扩展路径异常：{path}')
    if paths[-1].exists() and not paths[-1].is_file():
        raise ValueError('Fcitx 配置需要是普通文件。')


def fingerprint(path):
    if path.is_symlink():
        return ['symlink', os.readlink(path)]
    if path.is_dir():
        return {item.name: fingerprint(item) for item in path.iterdir()}
    if path.is_file():
        return [stat.S_IMODE(path.stat().st_mode), hashlib.sha256(path.read_bytes()).hexdigest()]
    if path.exists():
        raise ValueError(f'扩展目录包含特殊文件：{path}')
    return None


def copy(source, target):
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def replace(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.tb710fu-repair-', dir=path.parent) as directory:
        old = Path(directory)/'old'
        new = Path(directory)/'new'
        if source is not None:
            copy(source, new)
        present = path.exists()
        if present:
            path.rename(old)
        try:
            if source is not None:
                new.rename(path)
        except OSError:
            if present:
                old.rename(path)
            raise


def select(current):
    enabled = list(current[KEYS[0]])
    enabled.extend(uuid for uuid in UUIDS if uuid not in enabled)
    return {KEYS[0]: enabled, KEYS[1]: [uuid for uuid in current[KEYS[1]] if uuid not in UUIDS]}


def restore(snapshot, paths, record):
    validate_targets(paths)
    for index, path in enumerate(paths):
        saved = snapshot/str(index)
        replace(path, saved if record['original'][index] is not None else None)
    for key in KEYS:
        current = [uuid for uuid in settings(key) if uuid not in MANAGED]
        for index, uuid in record['selection'][key]:
            current.insert(index, uuid)
        set_settings(key, current)
    command('systemctl', '--user', 'daemon-reload')


def rollback(state, paths):
    snapshot_name = (state/'latest').read_text().strip()
    if not re.fullmatch(r'repair-[A-Za-z0-9_]+', snapshot_name):
        raise ValueError('无效的本机回退记录。')
    snapshot = state/snapshot_name
    record = json.loads((snapshot/'record.json').read_text())
    original = [str(path) for path in paths]
    if record['paths'] != original:
        raise ValueError('用户配置目录与安装时不同。')
    current = [fingerprint(path) for path in paths]
    if any(new != before and new != installed
           for new, before, installed in zip(current, record['original'], record['installed'])):
        raise ValueError('修复涉及的文件之后又被修改，已保留；请先核对本机快照。')
    restore(snapshot, paths, record)
    (state/'latest').unlink()
    print('已恢复修复前的文件与相关扩展选择。请保存工作后注销并重新登录。')


def apply(stage, paths, state):
    selection = {key: settings(key) for key in KEYS}
    desired = select(selection)
    config = paths[-1].read_text() if paths[-1].exists() else ''
    new_config = stage/'fcitx-config'
    new_config.write_text(preedit_config(config))
    new_config.chmod(stat.S_IMODE(paths[-1].stat().st_mode) if paths[-1].exists() else 0o600)
    sources = [stage/'extensions'/uuid for uuid in UUIDS] + [stage/file for file in SESSION_FILES] + [new_config]
    original = [fingerprint(path) for path in paths]
    installed = [fingerprint(source) for source in sources]
    if original == installed and selection == desired:
        print('整套修复已安装。')
        return
    snapshot = Path(tempfile.mkdtemp(prefix='repair-', dir=state))
    for index, path in enumerate(paths):
        if original[index] is not None:
            copy(path, snapshot/str(index))
    record = {
        'paths': [str(path) for path in paths], 'original': original, 'installed': installed,
        'selection': {key: [(i, uuid) for i, uuid in enumerate(values) if uuid in MANAGED]
                      for key, values in selection.items()},
    }
    (snapshot/'record.json').write_text(json.dumps(record))
    latest = state/'latest'
    previous = latest.read_text() if latest.exists() else None
    pointer = snapshot/'latest'
    pointer.write_text(snapshot.name + '\n')
    pointer.replace(latest)
    try:
        for path, source in zip(paths, sources):
            replace(path, source)
        for key in KEYS:
            set_settings(key, desired[key])
        command('systemctl', '--user', 'daemon-reload')
    except Exception:
        restore(snapshot, paths, record)
        if previous is None:
            latest.unlink()
        else:
            latest.write_text(previous)
        raise
    print('已应用完整虚拟键盘。请保存工作后注销并重新登录。')
    print('本机回退快照：' + str(snapshot))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'apply', 'rollback'), nargs='?', default='check')
    action = parser.parse_args().action
    preflight(action != 'rollback')
    paths, state = targets()
    validate_targets(paths)
    with tempfile.TemporaryDirectory(prefix='tb710fu-keyboard-check-') as temporary:
        stage = Path(temporary)
        if action != 'rollback':
            stage_bundle(stage)
        if action == 'check':
            if paths[-1].exists():
                preedit_config(paths[-1].read_text())
            select({key: settings(key) for key in KEYS})
            print('整包、Kimpanel schema 和 GNOME/Fcitx5 依赖检查通过。')
            return
        if state.is_symlink():
            raise ValueError('状态目录不能是符号链接。')
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.chmod(0o700)
        with (state/'lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if action == 'apply':
                apply(stage, paths, state)
            else:
                rollback(state, paths)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('操作停止：' + str(error), file=sys.stderr)
        sys.exit(1)
