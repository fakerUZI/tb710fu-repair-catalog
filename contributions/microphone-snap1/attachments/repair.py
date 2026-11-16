#!/usr/bin/env python3
"""Pinned TB710FU snap1 microphone repair. Default check; never reboots."""
import base64
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

HERE = Path(__file__).resolve().parent
VERSION = '7.2.6-tb710fu-snap1'
BEFORE = '6a588ec97a053d85afcf2b8dd8a62c479f50c7d6ec45552036c1a25e618fdf0e'
AFTER = 'b2c5db7b73afebf6b83a0b786000e768186c08e27daa6a8907bf24ff5d61bda2'
SIZE = 100663296
BASE = Path('/var/backups/tb710fu-microphone-snap1')
UNIT = 'tb-microphone-init.service'
FILES = {
    '/usr/local/sbin/tb-microphone-init': ('tb-microphone-init', 0o755),
    '/etc/systemd/system/tb-microphone-init.service': (UNIT, 0o644),
    '/etc/wireplumber/wireplumber.conf.d/60-tb710fu-microphone.conf':
        ('60-tb710fu-microphone.conf', 0o644),
}
MODULE_HASHES = {
    'snd-soc-wcd939x.ko': '53d85209ff648bfec0f900f4cb2291d13ae67e2e69c83551a819c8bf2f9879e0',
    'snd-soc-wcd939x-sdw.ko': 'd85c87ff737dd1a041fa14c71c74427b05d35ccdb15f751575449890588c5057',
}

class Stop(Exception):
    pass

def terminated(signum, frame):
    raise Stop("收到终止信号，尝试恢复本次事务。")

def require(ok, message):
    if not ok:
        raise Stop(message)

def run(args, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    require(not check or p.returncode == 0, '命令失败：' + ' '.join(args) + '\n' + p.stderr[-1000:])
    return p

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def safe_path(path):
    path = Path(path)
    for p in (path, *path.parents):
        require(not p.is_symlink(), '拒绝链接路径：' + str(p))
    return path

def atomic(path, data, mode=0o600):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tb-mic-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(tmp).unlink(missing_ok=True)

def normalize(tables, identity=False):
    result = []
    for wrapped in tables.values():
        t = copy.deepcopy(wrapped['partitiontable'])
        t.pop('device', None)
        if not identity:
            t.pop('id', None)
        for p in t['partitions']:
            p.pop('node', None)
            if not identity:
                p.pop('uuid', None)
            if p.get('name') == 'boot_a':
                def trim(m):
                    bits = [int(x) for x in m[1].split(',') if int(x) not in (51, 52, 53, 54)]
                    return 'GUID:' + ','.join(map(str, bits)) if bits else ''
                p['attrs'] = re.sub(r'GUID:([0-9,]+)', trim, p.get('attrs', '')).strip()
                if not p['attrs']:
                    p.pop('attrs')
        result.append(t)
    return sorted(result, key=lambda x: json.dumps(x, sort_keys=True))

def payload():
    hashes = json.loads((HERE / 'payload-sha256.json').read_text())
    require(set(hashes) == {'repair.py', 'boot-delta.json', 'compatibility.json',
                            'tb-microphone-init', UNIT, '60-tb710fu-microphone.conf'}, '附件清单异常')
    for name, expected in hashes.items():
        p = HERE / name
        require(p.is_file() and not p.is_symlink() and sha(p) == expected, '附件校验失败：' + name)
    compat = json.loads((HERE / 'compatibility.json').read_text())
    require(compat['version'] == VERSION, '兼容性版本不匹配。')
    return compat

def environment():
    require(os.geteuid() == 0, '需要管理员权限；请通过修复助手授权或使用 sudo。')
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64', '仅支持 Ubuntu ARM64 平板。')
    require(platform.release() == VERSION, '内核不匹配：仅支持 ' + VERSION)
    require(re.search(r'^ID=ubuntu$', Path('/etc/os-release').read_text(), re.M), '仅支持 Ubuntu。')
    require(Path('/run/systemd/system').is_dir(), '需要实际 systemd 系统。')
    require(b'lenovo,tb710fu' in Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0'), '机型不匹配。')
    require('androidboot.slot_suffix=_a' in Path('/proc/cmdline').read_text().split(), '仅支持已验证的 A 槽。')
    for c in ('sfdisk', 'sgdisk', 'blockdev', 'findmnt', 'blkid', 'systemctl', 'amixer'):
        require(shutil.which(c), '缺少工具：' + c)
    for name, expected in MODULE_HASHES.items():
        p = Path('/lib/modules') / VERSION / 'kernel/sound/soc/codecs' / name
        require(p.is_file() and sha(p) == expected, 'WCD 原始模块不匹配：' + name)
    require(Path('/usr/local/sbin/tb-audio-init').is_file(), '缺少已验证的扬声器适配。')
    require(Path('/etc/wireplumber/wireplumber.conf.d/50-tb710fu-audio.conf').is_file(), '缺少既有 pro-audio 配置。')

def inventory(compat, allow_partial=False):
    tables = {}
    for e in sorted(Path('/sys/class/block').glob('sd*')):
        if not (e / 'partition').exists():
            dev = '/dev/' + e.name
            tables[dev] = json.loads(run(['sfdisk', '--json', dev]).stdout)
            run(['sgdisk', '--verify', dev])
    require(normalize(tables) == compat['gpt_layout'], '当前 GPT 不在已验证范围；未写入。')
    boot = Path('/dev/disk/by-partlabel/boot_a').resolve(strict=True)
    require(stat.S_ISBLK(boot.stat().st_mode), 'boot_a 不是块设备。')
    matches = [p for t in tables.values() for p in t['partitiontable']['partitions'] if p.get('name') == 'boot_a']
    require(len(matches) == 1 and Path(matches[0]['node']).resolve() == boot, 'GPT 与 boot_a 标签不一致。')
    require(int(run(['blockdev', '--getsize64', str(boot)]).stdout) == SIZE, 'boot_a 大小不匹配。')
    firmware = {}
    for name, expected in compat['firmware'].items():
        firmware[name] = sha(Path('/dev/disk/by-partlabel') / name)
        require(firmware[name] == expected, '当前固件不同：' + name)
    root = run(['findmnt', '-n', '-o', 'SOURCE,FSTYPE', '/']).stdout.split()
    require(len(root) == 2 and root[1] == 'ext4', '根文件系统不是 ext4。')
    require(Path(root[0]).resolve() == Path('/dev/disk/by-partlabel/arch').resolve(strict=True), '根分区不是当前适配的 arch 标签。')
    root_uuid = run(['blkid', '-s', 'UUID', '-o', 'value', root[0]]).stdout.strip()
    h = sha(boot)
    require(allow_partial or h in (BEFORE, AFTER), '启动镜像不是已验证基线或修复版；拒绝覆盖。')
    return dict(boot=str(boot), rdev=boot.stat().st_rdev, boot_sha256=h,
                gpt=tables, firmware=firmware, root_uuid=root_uuid)

def same_device(old, new):
    require(old['root_uuid'] == new['root_uuid'] and old['firmware'] == new['firmware'] and
            normalize(old['gpt'], True) == normalize(new['gpt'], True), '设备、GPT 或固件已改变。')

def patch_image(source, target, delta):
    require(delta['format'] == 1 and delta['block_size'] == 4096 and delta['size'] == SIZE and
            delta['before_sha256'] == BEFORE and delta['after_sha256'] == AFTER, '补丁格式异常。')
    require(Path(source).stat().st_size == SIZE and sha(source) == BEFORE, '补丁基线校验失败。')
    decoded = []; end = 0
    for item in delta['ranges']:
        off = item['offset']; b = base64.b64decode(item['data'], validate=True)
        require(type(off) is int and off % 4096 == 0 and off >= end and len(b) == 4096 and off + len(b) <= SIZE, '补丁范围异常。')
        decoded.append((off, b)); end = off + len(b)
    require(decoded and not Path(target).exists(), '输出必须为尚不存在的文件。')
    with Path(target).open('xb') as dst, Path(source).open('rb') as src:
        shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
        for off, b in decoded:
            dst.seek(off); dst.write(b)
        dst.flush(); os.fsync(dst.fileno())
    require(sha(target) == AFTER, '重建镜像校验失败；未写入设备。')

def copy_full(src, dst):
    """Raw block writes may be short; do not silently skip unwritten bytes."""
    for block in iter(lambda: src.read(4 * 1024 * 1024), b''):
        view = memoryview(block)
        while view:
            count = dst.write(view)
            require(isinstance(count, int) and count > 0 and count <= len(view), '启动分区写入没有进展。')
            view = view[count:]

def write_boot(source, inv, before, after):
    require(Path(source).stat().st_size == SIZE and sha(source) == after, '写入源校验失败。')
    fd = os.open(inv['boot'], os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b', buffering=0) as dst:
        require(os.fstat(dst.fileno()).st_rdev == inv['rdev'] and sha(inv['boot']) == before, '启动设备在写入前改变。')
        with Path(source).open('rb') as src:
            copy_full(src, dst)
        os.fsync(dst.fileno()); dst.seek(0)
        h = hashlib.sha256()
        for b in iter(lambda: dst.read(4 * 1024 * 1024), b''): h.update(b)
        require(h.hexdigest() == after, '启动分区完整回读失败。')

def restore_boot(source, inv, expected):
    require(Path(source).stat().st_size == SIZE and sha(source) == expected, '恢复镜像校验失败。')
    fd = os.open(inv['boot'], os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b', buffering=0) as dst, Path(source).open('rb') as src:
        require(os.fstat(dst.fileno()).st_rdev == inv['rdev'], '恢复目标改变，已保留备份。')
        copy_full(src, dst)
        os.fsync(dst.fileno())
    require(sha(inv['boot']) == expected, '自动恢复未通过；不要重启，请核对私有备份。')

def verify_file_backups(work, state):
    require(set(state['files']) == set(FILES), '配置回退目标清单异常。')
    for dst, record in state['files'].items():
        safe_path(dst)
        if record is not None:
            require(isinstance(record, dict) and set(record) == {'backup', 'sha256', 'mode'} and re.fullmatch(r'config-[0-9]+', record['backup']), '配置备份记录异常。')
            source = safe_path(work / record['backup'])
            require(source.is_file() and sha(source) == record['sha256'], '配置备份校验失败。')

def restore_files(work, state):
    verify_file_backups(work, state)
    if not state['enabled']:
        run(['systemctl', 'disable', UNIT], False)
    for dst, record in state['files'].items():
        p = safe_path(dst)
        if record is None:
            p.unlink(missing_ok=True)
        else:
            source = work / record['backup']
            require(sha(source) == record['sha256'], '配置备份校验失败。')
            atomic(p, source.read_bytes(), record['mode'])
    run(['systemctl', 'daemon-reload'])
    if state['enabled']:
        run(['systemctl', 'enable', UNIT])
    if state.get('active'):
        run(['systemctl', 'start', UNIT])
    else:
        run(['systemctl', 'stop', UNIT], False)

def apply(compat, inv):
    active = BASE / 'active.json'
    if active.exists():
        state = json.loads(safe_path(active).read_text())
        same_device(state['before'], inv)
        require(inv['boot_sha256'] == AFTER and all(safe_path(dst).is_file() and sha(dst) == sha(HERE / src) and stat.S_IMODE(Path(dst).stat().st_mode) == mode for dst, (src, mode) in FILES.items()), '已有安装状态发生变化；请先检查备份。')
        print('已安装同一修复；未重复写入。请重启或检查麦克风。'); return
    safe_path(BASE); BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(BASE.stat().st_uid == 0 and stat.S_IMODE(BASE.stat().st_mode) == 0o700, '备份目录必须归 root 所有且权限为 700。')
    require(shutil.disk_usage(BASE).free > SIZE * 3, '备份/重建至少需要约 300 MiB 空间。')
    work = Path(tempfile.mkdtemp(prefix='repair-', dir=BASE))
    state = dict(work=work.name, before=inv, files={}, enabled=run(['systemctl', 'is-enabled', UNIT], False).stdout.strip() == 'enabled', active=run(['systemctl', 'is-active', UNIT], False).stdout.strip() == 'active')
    # Save everything privately before modifying the boot partition or configuration.
    backup = work / 'boot-before.img'
    with Path(inv['boot']).open('rb') as src, backup.open('xb') as dst:
        shutil.copyfileobj(src, dst, 4 * 1024 * 1024); dst.flush(); os.fsync(dst.fileno())
    backup.chmod(0o600)
    require(sha(backup) == inv['boot_sha256'], '启动备份校验失败。')
    for i, dst in enumerate(FILES):
        p = safe_path(dst)
        if p.exists():
            require(p.is_file(), '配置不是常规文件：' + dst)
            name = 'config-' + str(i); atomic(work / name, p.read_bytes())
            state['files'][dst] = dict(backup=name, sha256=sha(work / name), mode=stat.S_IMODE(p.stat().st_mode))
        else:
            state['files'][dst] = None
    atomic(work / 'state.json', json.dumps(state, ensure_ascii=False, indent=2).encode())
    image = backup
    if inv['boot_sha256'] == BEFORE:
        image = work / 'boot-repaired.img'
        patch_image(backup, image, json.loads((HERE / 'boot-delta.json').read_text()))
    now = inventory(compat); same_device(inv, now)
    require(now['boot_sha256'] == inv['boot_sha256'], '启动分区在检查后改变。')
    touched_boot = False
    try:
        for dst, (src, mode) in FILES.items(): atomic(dst, (HERE / src).read_bytes(), mode)
        run(['systemctl', 'daemon-reload']); run(['systemctl', 'enable', UNIT])
        if inv['boot_sha256'] == BEFORE:
            touched_boot = True
            write_boot(image, now, BEFORE, AFTER)
        atomic(active, json.dumps(state, ensure_ascii=False, indent=2).encode())
    except BaseException:
        if touched_boot:
            recovery = inventory(compat, allow_partial=True)
            same_device(inv, recovery)
            restore_boot(backup, recovery, inv['boot_sha256'])
        restore_files(work, state)
        active.unlink(missing_ok=True)
        raise
    print('修复已写入并校验。请保存工作后自行重启；本脚本不会自动重启。')
    print('本机私有备份：' + str(work))

def rollback(compat, inv):
    active = safe_path(BASE / 'active.json')
    require(active.is_file(), '没有本安装器的回退记录；不会使用其他历史备份。')
    state = json.loads(active.read_text()); name = state['work']
    require(re.fullmatch(r'repair-[A-Za-z0-9_-]+', name), '回退目录异常。')
    work = safe_path(BASE / name); same_device(state['before'], inv)
    require(inv['boot_sha256'] == AFTER, '当前启动镜像改变；拒绝覆盖。')
    for dst, (src, _) in FILES.items():
        require(Path(dst).is_file() and not Path(dst).is_symlink() and sha(dst) == sha(HERE / src), '配置在安装后已修改：' + dst)
    backup = work / 'boot-before.img'; before = state['before']['boot_sha256']
    require(before in (BEFORE, AFTER) and sha(backup) == before, '私有备份校验失败。')
    verify_file_backups(work, state)
    # Prepare a verified copy of the current repaired image before rollback.
    # It restores the pre-rollback state if the reverse write is interrupted.
    recovery_image = backup
    if before != AFTER:
        recovery_image = work / 'rollback-recovery.img'
        if recovery_image.exists():
            require(sha(recovery_image) == AFTER, '回退恢复镜像校验失败。')
        else:
            patch_image(backup, recovery_image, json.loads((HERE / 'boot-delta.json').read_text()))
    current_files = {dst: (Path(dst).read_bytes(), stat.S_IMODE(Path(dst).stat().st_mode)) for dst in FILES}
    current_enabled = run(['systemctl', 'is-enabled', UNIT], False).stdout.strip() == 'enabled'
    current_active = run(['systemctl', 'is-active', UNIT], False).stdout.strip() == 'active'
    now = inventory(compat); same_device(inv, now)
    require(now['boot_sha256'] == AFTER, '回退前启动镜像改变。')
    touched_boot = False
    try:
        run(['systemctl', 'stop', UNIT], False)
        if before != AFTER:
            touched_boot = True
            write_boot(backup, now, AFTER, before)
        restore_files(work, state)
        active.unlink()
    except BaseException:
        if touched_boot:
            recovery = inventory(compat, allow_partial=True)
            same_device(inv, recovery)
            restore_boot(recovery_image, recovery, AFTER)
        for dst, (data, mode) in current_files.items(): atomic(dst, data, mode)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable' if current_enabled else 'disable', UNIT])
        run(['systemctl', 'start' if current_active else 'stop', UNIT], current_active)
        atomic(active, json.dumps(state, ensure_ascii=False, indent=2).encode())
        raise
    print('已恢复本安装器运行前的启动镜像与配置。请保存工作后自行重启。')

def main():
    action = sys.argv[1] if len(sys.argv) == 2 else 'check'
    require(action in ('check', 'apply', 'rollback'), '未知操作。')
    compat = payload(); environment(); inv = inventory(compat)
    print('检查通过：TB710FU / ' + VERSION + ' / 当前固件与 GPT / 已验证启动镜像。')
    if action == 'check':
        print('只读检查完成，未修改系统。需应用时设置 TB710FU_FIX_ACTION=apply。'); return
    # Turn a normal termination request into a catchable transaction error.
    signal.signal(signal.SIGTERM, terminated)
    safe_path(BASE); BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(BASE.stat().st_uid == 0 and stat.S_IMODE(BASE.stat().st_mode) == 0o700, '备份目录权限异常。')
    with safe_path(BASE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inv = inventory(compat)
        (apply if action == 'apply' else rollback)(compat, inv)

if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print('停止：' + str(e), file=sys.stderr); sys.exit(1)
