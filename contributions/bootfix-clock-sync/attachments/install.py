#!/usr/bin/python3
"""Switch the BOOTFIX HTTP clock timer to Ubuntu's systemd-timesyncd."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PACKAGE = 'systemd-timesyncd'
SERVICE = PACKAGE + '.service'
OLD_TIMER = 'tb-timesync.timer'
OLD_SERVICE = 'tb-timesync.service'
DROP_IN = Path('/etc/systemd/timesyncd.conf.d/60-tb710fu-clock-sync.conf')
CONFIG = b'[Time]\nFallbackNTP=ntp.aliyun.com ntp.ubuntu.com time.cloudflare.com\n'
STATE_DIR = Path('/var/lib/tb710fu-clock-sync')
LOCK = Path('/run/lock/tb710fu-clock-sync.lock')
STOCK_VERSION = '259.5-0ubuntu3.4'
STOCK_FILE = f'{PACKAGE}_{STOCK_VERSION}_arm64.deb'
STOCK_SHA = '7191f7cde87d64bb708dee5c9e08fa02af00b034c5dbd17146215fe216ae74c6'
OTHER_PROVIDERS = ('chrony', 'ntpsec', 'ntp', 'openntpd', 'ptpd')


def call(*args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'APT_LISTCHANGES_FRONTEND': 'none'})


def run(*args, timeout=20):
    result = call(*args, timeout=timeout)
    if result.returncode:
        detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
        raise RuntimeError(f'{args[0]} 退出状态 {result.returncode}。{detail}')
    return result.stdout.strip()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def installed_version(name):
    result = call('dpkg-query', '-W', '-f=${db:Status-Status} ${Version}', name)
    status, _, version = result.stdout.strip().partition(' ')
    return version if result.returncode == 0 and status == 'installed' else None


def preflight():
    require(os.geteuid() == 0, '请通过修复助手或 sudo 运行。')



def loaded(unit):
    return call('systemctl', 'show', '-p', 'LoadState', '--value', unit).stdout.strip() == 'loaded'


def unit_state(unit):
    if not loaded(unit):
        return {'enabled': False, 'active': False}
    enabled = call('systemctl', 'is-enabled', unit).stdout.strip()
    active = call('systemctl', 'is-active', unit).stdout.strip()
    require(enabled in ('enabled', 'disabled', 'not-found') and active in ('active', 'inactive', 'failed'),
            f'{unit} 的状态无法安全保存，请保留当前配置。')
    return {'enabled': enabled == 'enabled', 'active': active == 'active'}


def load_state():
    directory = STATE_DIR
    require(not directory.is_symlink(), '状态目录是符号链接。')
    state_file = directory / 'state.json'
    require(not state_file.is_symlink(), '状态文件是符号链接。')
    if not state_file.exists():
        return None
    original = json.loads(state_file.read_text())
    require(set(original) == {'legacy', 'native'} and all(
        isinstance(value, dict) and set(value) == {'enabled', 'active'} and
        all(type(flag) is bool for flag in value.values()) for value in original.values()), '修复状态文件无效。')
    return original


def atomic_write(path, data, mode):
    require(not path.parent.is_symlink(), f'不能写入符号链接目录 {path.parent}。')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.tb-clock-sync-', delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


def check_config(state):
    require(not DROP_IN.parent.is_symlink() and not DROP_IN.is_symlink(), '时间同步配置位于符号链接路径。')
    if DROP_IN.exists():
        require(state is not None and DROP_IN.is_file() and DROP_IN.read_bytes() == CONFIG,
                '同名时间同步配置已存在或被修改，请保留原文件。')


@contextmanager
def package_source():
    version = installed_version('libsystemd-shared:arm64')
    if version != STOCK_VERSION:
        yield PACKAGE
        return
    source = Path(__file__).with_name(STOCK_FILE)
    require(source.is_file() and not source.is_symlink(), '缺少 BOOTFIX 离线时间同步包。')
    data = source.read_bytes()
    require(hashlib.sha256(data).hexdigest() == STOCK_SHA, '离线时间同步包校验失败。')
    with tempfile.TemporaryDirectory(prefix='tb710fu-timesync-') as stage:
        directory = Path(stage)
        directory.chmod(0o755)
        deb = directory / STOCK_FILE
        deb.write_bytes(data)
        deb.chmod(0o644)
        yield str(deb)


def install_timesyncd(source):
    options = ('apt-get', '-o', 'Acquire::Retries=1', '--no-install-recommends', '--no-remove', '-y')
    run(*options, 'install', source, timeout=180)


def restore(unit, state):
    run('systemctl', 'enable' if state['enabled'] else 'disable', unit)
    run('systemctl', 'start' if state['active'] else 'stop', unit)


def rollback(previous):
    native_loaded = call('systemctl', 'show', '-p', 'LoadState', '--value', SERVICE).stdout.strip() == 'loaded'
    if native_loaded:
        run('systemctl', 'stop', SERVICE)
    DROP_IN.unlink(missing_ok=True)
    if native_loaded:
        restore(SERVICE, previous['native'])
    if loaded(OLD_TIMER):
        restore(OLD_TIMER, previous['legacy'])
    (STATE_DIR / 'state.json').unlink()


def enable_native(source):
    if source is not None:
        install_timesyncd(source)
    if loaded(OLD_TIMER):
        run('systemctl', 'disable', '--now', OLD_TIMER)
    if loaded(OLD_SERVICE):
        run('systemctl', 'stop', OLD_SERVICE)
    atomic_write(DROP_IN, CONFIG, 0o644)
    run('systemctl', 'enable', SERVICE)
    run('systemctl', 'restart', SERVICE)
    run('systemctl', 'is-active', '--quiet', SERVICE)


def apply(previous):
    if previous is None:
        previous = {'legacy': unit_state(OLD_TIMER), 'native': unit_state(SERVICE)}
        atomic_write(STATE_DIR / 'state.json', (json.dumps(previous) + '\n').encode(), 0o600)
    try:
        if installed_version(PACKAGE):
            enable_native(None)
        else:
            with package_source() as source:
                enable_native(source)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        try:
            rollback(previous)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print(f'自动恢复未全部完成，可使用回退重试：{error}', file=sys.stderr)
        raise


@contextmanager
def exclusive():
    fd = os.open(LOCK, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def operate(action):
    state = load_state()
    if action != 'rollback' and state is None:
        for name in OTHER_PROVIDERS:
            if installed_version(name):
                print(f'沿用已有校时组件 {name}。')
                return
    check_config(state)
    if action == 'check':
        if state is None:
            print('正在检查当前系统时间同步状态。')
        else:
            print('已保存 NTP 配置。' if DROP_IN.exists() else '发现待收尾的修复状态，可重新应用或回退。')
        print(run('timedatectl', 'show', '-p', 'NTP', '-p', 'NTPSynchronized'))
    elif action == 'apply':
        apply(state)
        print('已启动 NTP 校时。首次同步需等待时间源回应。')
        print(run('timedatectl', 'show', '-p', 'NTP', '-p', 'NTPSynchronized'))
    elif state is not None:
        rollback(state)
        print('已恢复应用前的时钟服务启用及运行状态。')
    else:
        print('未应用本条修复。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'apply', 'rollback'))
    try:
        action = parser.parse_args().action
        preflight()
        if action == 'check':
            operate(action)
        else:
            with exclusive():
                operate(action)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
