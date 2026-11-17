#!/usr/bin/env python3
"""TB710FU issue #19: bounded UCSI-cooperative host candidate and private-safe observations."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time

ROLE = 'class/usb_role/a600000.usb-role-switch/role'
PORT = 'class/typec/port0'
DRIVER = 'bus/auxiliary/drivers/ucsi_glink.pmic_glink_ucsi'
DEVICE = 'pmic_glink.ucsi.0'
SERVICE = 'tb-ubuntu-usb-ncm.service'


def read(path):
    try:
        return path.read_text().strip().replace('\x00', '')
    except OSError:
        return None


def selected(value):
    if not value:
        return None
    match = re.search(r'\[([^]]+)\]', value)
    return match[1] if match else value


class Probe:
    def __init__(self, sysroot=Path('/sys'), config=Path('/sys/kernel/config'),
                 run=subprocess.run, clock=time.monotonic, sleep=time.sleep):
        self.sys = sysroot
        self.config = config
        self.run = run
        self.clock = clock
        self.sleep = sleep

    def snapshot(self):
        port = self.sys / PORT
        devices = []
        # Deliberately omit USB serials, product strings, UUIDs, account and network data.
        for dev in sorted((self.sys / 'bus/usb/devices').glob('*')):
            vendor, product = read(dev / 'idVendor'), read(dev / 'idProduct')
            if not vendor or not product or dev.name.startswith('usb'):
                continue
            if not re.fullmatch('[0-9a-fA-F]{4}', vendor + '') or not re.fullmatch('[0-9a-fA-F]{4}', product):
                continue
            devices.append({'vid_pid': vendor + ':' + product, 'speed': read(dev / 'speed')})
        return {'role': read(self.sys / ROLE),
                'ucsi_bound': (self.sys / DRIVER / DEVICE).is_symlink(),
                'partner': (self.sys / 'class/typec/port0-partner').exists(),
                'data_role': selected(read(port / 'data_role')),
                'power_role': selected(read(port / 'power_role')),
                'orientation': read(port / 'orientation'),
                'power_operation_mode': read(port / 'power_operation_mode'),
                'usb': devices}

    def validate(self):
        model = read(self.sys / 'firmware/devicetree/base/model')
        if not model or 'TB710FU' not in model:
            raise RuntimeError('仅支持已核对型号的 TB710FU。')
        if platform.machine() != 'aarch64' or not re.fullmatch(r'7\.2\.6-tb710fu(?:-[A-Za-z0-9.-]+)?', platform.release()):
            raise RuntimeError('仅支持 ARM64 / 7.2.6-tb710fu 系列；其他内核需另行审核。')
        if not (self.sys / ROLE).exists():
            raise RuntimeError('目标 USB role switch 不存在。')
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                       if '=' in line)
        if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '26.04':
            raise RuntimeError('仅审核过 Ubuntu 26.04；其他系统请先复核服务与接口。')

    def preflight_host(self):
        if not (self.sys / DRIVER / DEVICE).is_symlink():
            raise RuntimeError('UCSI 已解绑。先在平板本机执行 sudo tb-usbmode rebind，确认 port0 恢复；不要使用旧 host 动作。')
        # Avoid dropping the rescue link even when sudo strips SSH_CONNECTION.
        if read(self.sys / 'class/net/usb0/carrier') == '1':
            raise RuntimeError('USB 救援链路仍连接；请在平板本机或 Wi-Fi 会话中断开 PC 后测试。')
        result = self.run(['systemctl', 'show', SERVICE, '--property=ActiveState', '--value'],
                          capture_output=True, text=True, timeout=5, check=True)
        if result.stdout.strip() not in ('inactive', 'failed'):
            raise RuntimeError('USB gadget 服务仍可能写回 device。先在本机停止 tb-ubuntu-usb-ncm.service；本工具不修改服务。')
        for udc in (self.config / 'usb_gadget').glob('*/UDC'):
            value = read(udc)
            if value is None or value:
                raise RuntimeError('gadget 仍绑定 UDC 或不可读取。先安全断开 gadget；本工具不解绑 gadget。')

    def host(self, seconds, emit=print):
        self.preflight_host()
        deadline = self.clock() + seconds
        requested = False
        host_since = None
        enumerated = False
        # A pre-existing downstream device cannot be counted as newly enumerated.
        if self.snapshot()['usb']:
            raise RuntimeError('已有下游 USB 设备；先安全卸载并拔出，再开始直连测试。')
        emit('等待插入 SSD；只通过 UCSI data_role 请求一次 host，不强制电源角色。')
        while self.clock() < deadline:
            state = self.snapshot()
            emit(json.dumps(state, ensure_ascii=False, sort_keys=True))
            if not state['ucsi_bound']:
                raise RuntimeError('测试中 UCSI 被解绑；停止请求角色。')
            if state['partner']:
                if state['data_role'] != 'host' and not requested:
                    # Set the guard before IO: a rejected DR_SWAP must not be retried in a loop.
                    requested = True
                    (self.sys / PORT / 'data_role').write_text('host\n')
                if state['data_role'] == 'host' and state['role'] == 'host':
                    if state['usb']:
                        host_since = self.clock() if host_since is None else host_since
                    else:
                        host_since = None
                    enumerated = bool(state['usb'])
                    if enumerated and host_since is not None and self.clock() - host_since >= 3:
                        emit('观察到下游 USB 枚举与 host 持续至少 3 秒；仍须人工核对 SSD、挂载与读文件。')
                        return 0
                else:
                    host_since = None
                    enumerated = False
            else:
                host_since = None
                enumerated = False
            self.sleep(0.1)
        emit('未解决：没有在时间窗内观察到稳定 host 与下游枚举。不能据此判定 PPM 根因。')
        return 3

    def watch(self, seconds, emit=print):
        start = self.clock()
        while self.clock() - start < seconds:
            emit(json.dumps({'t': round(self.clock() - start, 3), **self.snapshot()},
                            sort_keys=True, ensure_ascii=False))
            self.sleep(0.1)
        return 0


class Trace:
    """Use a private tracefs instance. Never clear or consume the global trace."""
    EVENTS = ('ucsi_connector_change', 'ucsi_run_command', 'ucsi_register_port')

    def __init__(self, root=Path('/sys/kernel/tracing')):
        self.root = root
        self.instance = root / 'instances' / ('tb710fu-usb-' + str(os.getpid()))
        self.created = False

    def __enter__(self):
        if not (self.root / 'instances').is_dir():
            raise RuntimeError('tracefs 未挂载或不支持 instances；本工具不自动挂载。')
        self.instance.mkdir(mode=0o700)
        self.created = True
        try:
            (self.instance / 'tracing_on').write_text('0\n')
            (self.instance / 'buffer_size_kb').write_text('256\n')
            for name in self.EVENTS:
                (self.instance / 'events/ucsi' / name / 'enable').write_text('1\n')
            (self.instance / 'tracing_on').write_text('1\n')
        except BaseException:
            self.close()
            raise
        return self

    def report(self):
        (self.instance / 'tracing_on').write_text('0\n')
        # Drop process names/PIDs and all events outside our explicit allowlist.
        for line in (self.instance / 'trace').read_text().splitlines():
            match = re.search(r'\b(\d+\.\d+): (ucsi_[a-z_]+): (.*)$', line)
            if match and match[2] in self.EVENTS:
                print(json.dumps({'trace_t': match[1], 'event': match[2], 'status': match[3]}))

    def close(self):
        if self.created:
            try:
                (self.instance / 'tracing_on').write_text('0\n')
            finally:
                self.instance.rmdir()
                self.created = False

    def __exit__(self, *args):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'watch', 'host', 'trace'], nargs='?', default='check')
    parser.add_argument('--seconds', type=int, choices=range(5, 121), default=30, metavar='5..120')
    args = parser.parse_args()
    probe = Probe()
    probe.validate()
    if args.action == 'check':
        print(json.dumps({'kernel': platform.release(), **probe.snapshot()}, ensure_ascii=False, indent=2))
        print('只读检查完成；不代表 SSD 已修复。')
        return 0
    if args.action == 'watch':
        return probe.watch(args.seconds)
    if os.geteuid() != 0:
        raise RuntimeError('host / trace 需要管理员权限。')
    # Lock in a root-owned directory, never follow an attacker-created lock symlink.
    fd = os.open('/run/lock/tb710fu-usb-c-test.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != 0:
            raise RuntimeError('锁文件不属于 root。')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'trace':
            with Trace() as trace:
                try:
                    return probe.watch(args.seconds)
                finally:
                    trace.report()
        return probe.host(args.seconds)
    finally:
        os.close(fd)


if __name__ == '__main__':
    # SIGTERM follows the same cleanup path as Ctrl+C for private tracing state.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('错误：' + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('测试已中断；host 请求不会自动反向切换，以免断开已挂载存储。', file=sys.stderr)
        sys.exit(130)
