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


def target_id(value):
    if not re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{4}', value):
        raise argparse.ArgumentTypeError('目标必须是四位 VID:四位 PID，例如 152d:0562。')
    return value.lower()


class Probe:
    def __init__(self, sysroot=Path('/sys'), config=Path('/sys/kernel/config'),
                 run=subprocess.run, clock=time.monotonic, sleep=time.sleep):
        self.sys = sysroot
        self.config = config
        self.run = run
        self.clock = clock
        self.sleep = sleep

    def partner_generation(self):
        try:
            stat = (self.sys / 'class/typec/port0-partner').stat()
            return stat.st_dev, stat.st_ino
        except FileNotFoundError:
            return None

    def usb_generation(self, target):
        # Ephemeral bus path/address identifies one enumeration, never logged.
        generation = []
        for dev in (self.sys / 'bus/usb/devices').glob('*'):
            if dev.name.startswith('usb'):
                continue
            if f"{read(dev / 'idVendor')}:{read(dev / 'idProduct')}".lower() != target:
                continue
            address = read(dev / 'devnum')
            if address and address.isdecimal():
                generation.append((dev.name, address))
        return tuple(sorted(generation))

    def snapshot(self):
        port = self.sys / PORT
        devices = []
        # Deliberately omit USB serials, product strings, UUIDs, account and network data.
        for dev in sorted((self.sys / 'bus/usb/devices').glob('*')):
            vendor, product = read(dev / 'idVendor'), read(dev / 'idProduct')
            if not vendor or not product or dev.name.startswith('usb'):
                continue
            if not re.fullmatch('[0-9a-fA-F]{4}', vendor) or not re.fullmatch('[0-9a-fA-F]{4}', product):
                continue
            devices.append({'vid_pid': (vendor + ':' + product).lower(), 'speed': read(dev / 'speed')})
        return {'role': read(self.sys / ROLE),
                'ucsi_bound': (self.sys / DRIVER / DEVICE).is_symlink(),
                'partner': self.partner_generation() is not None,
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
        net = self.sys / 'class/net/usb0'
        carrier = read(net / 'carrier')
        if net.exists() and carrier not in ('0',):
            raise RuntimeError('USB 救援链路仍连接或状态不可读；请在平板本机或 Wi-Fi 会话中断开 PC 并核对状态。')
        result = self.run(['systemctl', 'show', SERVICE, '--property=ActiveState', '--value'],
                          capture_output=True, text=True, timeout=5, check=True)
        if result.stdout.strip() not in ('inactive', 'failed'):
            raise RuntimeError('USB gadget 服务仍可能写回 device。先在本机停止 tb-ubuntu-usb-ncm.service；本工具不修改服务。')
        gadgets = self.config / 'usb_gadget'
        if not gadgets.is_dir():
            raise RuntimeError('无法检查 configfs 的 usb_gadget；未请求角色切换。')
        for udc in gadgets.glob('*/UDC'):
            value = read(udc)
            if value is None or value:
                raise RuntimeError('gadget 仍绑定 UDC 或不可读取。先安全断开 gadget；本工具不解绑 gadget。')

    def host(self, seconds, emit=print, target='152d:0562'):
        self.preflight_host()
        start = self.clock()
        deadline = start + seconds
        requested = False
        host_since = None
        stable_generation = None
        partner_generation = None
        # A pre-existing downstream device cannot be counted as newly enumerated.
        initial = self.snapshot()
        if initial['usb']:
            raise RuntimeError('已有下游 USB 设备；先安全卸载并拔出，再开始直连测试。')
        if initial['partner'] or self.partner_generation() is not None:
            raise RuntimeError('已有 Type-C partner；先断开线材，从未连接状态开始测试。')
        emit(f'等待插入目标 {target}；只通过 UCSI data_role 请求一次 host，不强制电源角色。')
        while self.clock() < deadline:
            state = self.snapshot()
            now = self.clock()
            emit(json.dumps({'t': round(now - start, 3), 'monotonic': round(now, 6), **state},
                            ensure_ascii=False, sort_keys=True))
            if not state['ucsi_bound']:
                raise RuntimeError('测试中 UCSI 被解绑；停止请求角色。')
            generation = self.partner_generation()
            if state['partner'] != (generation is not None):
                raise RuntimeError('Type-C 连接正在变化；停止本次测试。')
            if partner_generation is not None and generation != partner_generation:
                raise RuntimeError('Type-C 连接发生变化；停止本次测试，不向新连接请求角色。')
            if generation is not None:
                partner_generation = generation
            if state['partner']:
                if state['data_role'] not in ('host', 'device'):
                    raise RuntimeError('无法确认 Type-C 数据角色；未请求切换。')
                if state['data_role'] == 'device' and not requested:
                    # Conditions may change during the insertion wait. Recheck immediately before IO.
                    self.preflight_host()
                    current = self.snapshot()
                    if not current['ucsi_bound'] or self.partner_generation() != partner_generation or not current['partner']:
                        raise RuntimeError('角色写入前 Type-C 连接发生变化；未请求切换。')
                    if current['data_role'] not in ('host', 'device'):
                        raise RuntimeError('角色写入前无法确认数据角色；未请求切换。')
                    if self.clock() >= deadline:
                        break
                    # Set the guard before IO: a rejected DR_SWAP must not be retried in a loop.
                    requested = True
                    if current['data_role'] == 'device':
                        (self.sys / PORT / 'data_role').write_text('host\n')
                if state['data_role'] == 'host' and state['role'] == 'host':
                    usb_generation = self.usb_generation(target)
                    if usb_generation and any(dev['vid_pid'] == target for dev in state['usb']):
                        if usb_generation != stable_generation:
                            stable_generation = usb_generation
                            host_since = now
                    else:
                        host_since = None
                        stable_generation = None
                    if host_since is not None and now - host_since >= 3:
                        emit(f'观察到目标 {target} 的同一次枚举与 host 持续至少 3 秒；仍须人工核对容量、挂载与读文件。')
                        return 0
                else:
                    host_since = None
                    stable_generation = None
            else:
                host_since = None
                stable_generation = None
            self.sleep(0.1)
        emit(f'未解决：没有在时间窗内观察到稳定 host 与目标 {target} 的同一次枚举。不能据此判定 PPM 根因。')
        return 3

    def watch(self, seconds, emit=print):
        start = self.clock()
        while self.clock() - start < seconds:
            now = self.clock()
            emit(json.dumps({'t': round(now - start, 3), 'monotonic': round(now, 6), **self.snapshot()},
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
            (self.instance / 'current_tracer').write_text('nop\n')
            (self.instance / 'events/enable').write_text('0\n')
            (self.instance / 'trace_clock').write_text('mono\n')
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
    parser.add_argument('--target', type=target_id, default='152d:0562', metavar='VID:PID',
                        help='host 成功判据的目标，默认 issue #19 的 152d:0562；不影响只读动作。')
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
    if args.action == 'trace':
        # Independent instances must be usable alongside the host trial.
        with Trace() as trace:
            try:
                return probe.watch(args.seconds)
            finally:
                trace.report()
    # Lock in a root-owned directory, never follow an attacker-created lock symlink.
    fd = os.open('/run/lock/tb710fu-usb-c-test.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != 0:
            raise RuntimeError('锁文件不属于 root。')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return probe.host(args.seconds, target=args.target)
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
