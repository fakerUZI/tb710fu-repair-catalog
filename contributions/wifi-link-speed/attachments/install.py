#!/usr/bin/python3
"""Let one opted-in network negotiate normally on the known TB710FU daemon."""

import argparse
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile


PROGRAM = Path('/usr/local/libexec/tb-wifi-compat.py')
SERVICE = 'tb-wifi-compat.service'
UNIT = Path('/etc/systemd/system') / SERVICE
PROGRAM_HASH = 'b4bc16fa8dd9c3babe540a4485d372b53c78af309278e1118d6d03898b2dcdf6'
UNIT_HASH = 'f083d00e727abb334822b2bddbb5dc8b2ccd59ad31657b3935f584dc8da80ca8'
LOCK = Path('/run/lock/tb710fu-wifi-link-speed.lock')
START = b'# BEGIN TB710FU wifi-link-speed\n'
END = b'# END TB710FU wifi-link-speed\n'
HANDLER = b'def apply(network, _values=None, path=None):\n'
SETTER = b'        # Integers must be typed as integers'
GUARD = b'        if use_normal_negotiation(network):\n            return\n'
HELPER = r'''


def use_normal_negotiation(network):
    network_id = str(network).rsplit('/', 1)[-1]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as control:
            control.settimeout(1)
            # The service and supplicant have separate temporary directories.
            control.bind(f'\0tb-wifi-compat-{os.getpid()}')
            control.connect('/run/wpa_supplicant/wlp1s0')
            control.send(f'GET_NETWORK {network_id} ssid'.encode('ascii'))
            return control.recv(256).rstrip(b'\r\n') == NORMAL_NEGOTIATION_SSID
    except OSError:
        return False


'''.encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ssid_value(value):
    # wpa_supplicant returns a quoted printable SSID or a hex-encoded SSID.
    if (isinstance(value, bytes) and
            (re.fullmatch(rb'"[\x20-\x7e]{1,32}"', value) or
             re.fullmatch(rb'(?:[0-9a-fA-F]{2}){1,32}', value))):
        return value
    raise ValueError('无法读取当前 Wi-Fi 名称的有效表示。')


def patched(original, ssid):
    block = START + b'NORMAL_NEGOTIATION_SSID = ' + repr(ssid_value(ssid)).encode('ascii') + HELPER + END
    result = original.replace(HANDLER, block + HANDLER, 1).replace(SETTER, GUARD + SETTER, 1)
    compile(result, str(PROGRAM), 'exec')
    return result


def original_and_selection(current):
    if digest(current) == PROGRAM_HASH:
        return current, None
    prefix, start, rest = current.partition(START)
    block, end, suffix = rest.partition(END)
    original = (prefix + suffix).replace(GUARD, b'', 1)
    if start and end and digest(original) == PROGRAM_HASH:
        setting = block.split(b'\n', 1)[0].removeprefix(b'NORMAL_NEGOTIATION_SSID = ')
        try:
            ssid = ssid_value(ast.literal_eval(setting.decode('ascii')))
        except (UnicodeError, ValueError, SyntaxError):
            pass
        else:
            if patched(original, ssid) == current:
                return original, ssid
    raise ValueError('检测到不同版本或手工修改的 Wi-Fi 兼容脚本，请保留并检查现有配置。')


def command(*args):
    result = subprocess.run(args, capture_output=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} 退出状态为 {result.returncode}。')
    return result.stdout.rstrip(b'\r\n')


def wpa(*args):
    return command('/usr/sbin/wpa_cli', '-i', 'wlp1s0', *args)


def current_selection():
    status = dict(line.split(b'=', 1) for line in wpa('status').splitlines() if b'=' in line)
    if status.get(b'wpa_state') != b'COMPLETED' or not status.get(b'id', b'').isdigit():
        raise ValueError('请先连接需要修复的 Wi-Fi。')
    ident = status[b'id'].decode('ascii')
    return ssid_value(wpa('get_network', ident, 'ssid')), wpa('get_network', ident, 'disable_vht')


def owned_file(path):
    mode = path.lstat()
    if not stat.S_ISREG(mode.st_mode) or mode.st_uid != 0 or mode.st_mode & 0o022:
        raise ValueError(f'无法安全使用 {path}。')
    return stat.S_IMODE(mode.st_mode)


def atomic_write(path, data, mode):
    with NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
        staging = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            os.replace(staging, path)
        finally:
            staging.unlink(missing_ok=True)


def restart():
    command('/usr/bin/systemctl', 'restart', SERVICE)
    command('/usr/bin/systemctl', 'is-active', '--quiet', SERVICE)


def change(previous, target, mode):
    if previous == target:
        restart()
        return
    if PROGRAM.read_bytes() != previous:
        raise ValueError('兼容脚本在操作期间已变化。')
    atomic_write(PROGRAM, target, mode)
    try:
        restart()
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        if PROGRAM.read_bytes() != target:
            raise RuntimeError('重启失败，检测到文件已被另一个操作改动，请检查兼容服务。') from None
        atomic_write(PROGRAM, previous, mode)
        try:
            restart()
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise RuntimeError('已恢复原文件，兼容服务仍无法启动。') from None
        raise RuntimeError('兼容服务重启失败，已恢复原文件和服务。') from None


def preflight():
    if os.geteuid() != 0:
        raise ValueError('请通过修复助手或 sudo 运行。')
    if b'lenovo,tb710fu' not in Path('/proc/device-tree/compatible').read_bytes().split(b'\0'):
        raise ValueError('此修复仅适用于 TB710FU。')
    os_release = Path('/etc/os-release').read_text().splitlines()
    if not {'ID=ubuntu', 'ID="ubuntu"'} & set(os_release):
        raise ValueError('此修复适用于 Ubuntu。')
    owned_file(UNIT)
    if digest(UNIT.read_bytes()) != UNIT_HASH:
        raise ValueError('未找到已验证版本的 Wi-Fi 兼容服务。')
    if command('/usr/bin/systemctl', 'show', '-p', 'FragmentPath', '--value', SERVICE) != os.fsencode(UNIT):
        raise ValueError('系统正在使用另一份 Wi-Fi 兼容服务。')
    if command('/usr/bin/systemctl', 'show', '-p', 'DropInPaths', '--value', SERVICE):
        raise ValueError('兼容服务存在本机覆盖配置，请检查后再应用。')


@contextmanager
def exclusive():
    fd = os.open(LOCK, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def operate(action):
    mode = owned_file(PROGRAM)
    current = PROGRAM.read_bytes()
    original, installed = original_and_selection(current)
    if action == 'rollback':
        if installed is None:
            print('未安装此修复。')
            return
        change(current, original, mode)
        print('已恢复 Wi-Fi 兼容配置。请在平板上重新连接 Wi-Fi。')
        return
    ssid, disabled = current_selection()
    if installed is not None and installed != ssid:
        raise ValueError('另一 Wi-Fi 已设为例外。请先回退，再连接要修复的网络。')
    if installed is None and disabled != b'1':
        raise ValueError('当前连接未发现兼容服务的速率限制。')
    if action == 'check':
        print('当前 Wi-Fi 已保存例外。' if installed else '发现 Wi-Fi 兼容服务速率限制，可应用修复。')
        return
    change(current, patched(original, ssid), mode)
    print('已保存当前 Wi-Fi 的例外。请在平板上重新连接 Wi-Fi 后检查速率。')


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
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
