#!/usr/bin/env python3
"""Upgrade the known TB710FU Ubuntu installation from inside Ubuntu.

Use the complete, checksum-pinned offline bundle, not this source file alone.
--check is read-only. --install stages modules, backs up boot/GPT, writes boot,
and enables post-boot Snap repair. Reboot is explicit unless --reboot is given.
"""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

VERSION = '7.2.6-tb710fu-20261118'
RELEASE_PLAN = {'format': 2, 'source_policy': 'undated-or-dated-before-target', 'target_version': VERSION}
BOOT_HASH = 'BUILD_TIME_BOOT_SHA256'
OLD_HASHES = {
    '8ef8203624371525150904700f7ffab7aec5f72e66a66cb62d74733f7603a4c9',
    '64c9d426065ff9a6150c246ab63b8ae3e8960b4b7a668a2e090370fc80e5f5ec',
    'b2c5db7b73afebf6b83a0b786000e768186c08e27daa6a8907bf24ff5d61bda2',
    '49e01404b2f7725a3e04d4161089f7bbc6b86078dad60c82f237dbd070e7f16a',
    '820d71fce5046b594a0c802623e9a658b3b6f6fa0f976f0090c6f76e9b8258c9',
}
BOOT_SIZE = 100663296
# Filled in the built bundle. A source checkout alone intentionally cannot flash.
MANIFEST_SHA256 = 'BUILD_TIME_MANIFEST_SHA256'
BASE = Path('/var/backups/tb710fu-kernel-upgrade') / VERSION
ACTIVE = BASE / 'kernel-upgrade-active'
UNIT = 'tb710fu-kernel-upgrade-20261118.service'
UNIT_PATH = Path('/etc/systemd/system') / UNIT
HELPERS = ('tb_camera_overlay.ko', 'tb_camera_alias.ko', 'tb_camss_alias.ko', 'tb_focus_manual.ko')
FILES = {
    '/opt/tb710fu-camera-test/tb-camera-live.sh': 'tb-camera-live.sh',
    '/opt/tb710fu-camera-test/tb-camera-focus.sh': 'tb-camera-focus.sh',
    '/etc/modprobe.d/91-tb710fu-camera-manual.conf': '91-tb710fu-camera-manual.conf',
    '/usr/local/sbin/tb-ubuntu-usb-ncm': 'tb-ubuntu-usb-ncm',
    '/usr/local/libexec/tb-usb-ncm-identity.py': 'tb-usb-ncm-identity.py',
    '/etc/modules-load.d/90-tb710fu-netfilter.conf': '90-tb710fu-netfilter.conf',
    '/usr/local/bin/tb-panel-cycle-py': 'tb-panel-cycle.py',
    '/etc/systemd/system/tb-panel-cycle.service.d/90-dsi-card.conf': '90-dsi-card.conf',
    '/usr/local/libexec/tb710fu-power-key.py': 'tb710fu-power-key.py',
    '/etc/systemd/user/tb-gnome-power-button.service.d/90-lock-first.conf': '90-lock-first.conf',
    '/etc/tmpfiles.d/tb710fu-package-dirs.conf': 'tb710fu-package-dirs.conf',
    '/etc/dpkg/dpkg.cfg.d/90-tb710fu-package-dirs': '90-tb710fu-package-dirs',
    '/etc/systemd/system/tb710fu-dp-early-guard.service.d/80-kernel-profile.conf': '80-kernel-profile.conf',
    '/etc/systemd/system/tb710fu-kernel-upgrade-20261117.service.d/90-source-version.conf': '90-source-version.conf',
}
OPTIONAL_FILES = {
    '/opt/tb710fu-front-camera/tb-front-camera.sh': 'tb-front-camera.sh',
}
EXECUTABLES = {'tb-camera-live.sh', 'tb-camera-focus.sh', 'tb-front-camera.sh',
               'tb-ubuntu-usb-ncm', 'tb-usb-ncm-identity.py', 'tb-panel-cycle.py',
               'tb710fu-power-key.py'}
TRIAL_MASK = (7 << 51) | (1 << 54)


class Stop(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def log(message):
    print(message, flush=True)


def run(args, *, check=True, timeout=120):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and result.returncode:
        raise Stop('命令失败：' + ' '.join(map(str, args)) + '\n' + (result.stderr or result.stdout)[-2000:])
    return result


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.tb-upgrade-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def save_state(work, state):
    atomic(work / 'state.json', (json.dumps(state, indent=2, ensure_ascii=False) + '\n').encode())


def normalize_attrs(value, ignored_bits):
    def normalize_guid(match):
        bits = sorted(set(map(int, match[1].split(','))) - ignored_bits)
        return 'GUID:' + ','.join(map(str, bits)) if bits else ''
    return ' '.join(re.sub(r'\bGUID:([0-9]+(?:,[0-9]+)*)(?=\s|$)',
                           normalize_guid, value).split())


def normalized_gpt(tables, *, identity=False):
    """Retain geometry and safety flags. Allow documented A/B state to vary."""
    result = []
    for wrapped in tables.values():
        table = copy.deepcopy(wrapped['partitiontable'])
        table.pop('device', None)
        if not identity:
            table.pop('id', None)
        names = {part.get('name', '') for part in table['partitions']}
        for partition in table['partitions']:
            partition.pop('node', None)
            if not identity:
                partition.pop('uuid', None)
            name = partition.get('name', '')
            ignored = set()
            if name in {'boot_a', 'boot_b'}:
                ignored = {48, 49, 51, 52, 53, 54}
            elif name.endswith(('_a', '_b')) and name[:-1] + ('b' if name.endswith('_a') else 'a') in names:
                ignored = {48, 49, 51, 52, 53, 54}
            attrs = normalize_attrs(partition.pop('attrs', ''), ignored)
            if attrs:
                partition['attrs'] = attrs
        result.append(table)
    return sorted(result, key=lambda x: json.dumps(x, sort_keys=True))


def policy():
    path = Path(__file__).with_name('kernel-release.py')
    spec = importlib.util.spec_from_file_location('tb_kernel_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_source():
    policy().validate(RELEASE_PLAN, platform.release())


def depmod():
    mask = os.umask(0o022)
    try:
        run(['depmod', VERSION])
    finally:
        os.umask(mask)
    for index in (Path('/lib/modules') / VERSION).glob('modules.*'):
        if index.is_file():
            index.chmod(0o644)


def private_front_preflight(manifest):
    enabled = run(['systemctl', 'is-enabled', 'tb710fu-front-camera.service'], check=False).returncode == 0
    if enabled:
        overlay = Path('/lib/firmware/tb-front-raw.dtbo')
        require(overlay.is_file() and sha(overlay) == manifest['private_front_overlay_sha256'], '本机前摄 overlay 与当前适配不匹配。')
        root = Path('/opt/tb710fu-front-camera') / VERSION
        for name, expected in manifest['private_front_helpers'].items():
            path = root / name
            require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0,
                    '已启用前摄，但缺少新版本的本机私有模块：' + str(path))
            require(sha(path) == expected, '前摄私有模块与审核版本不一致：' + name)
            require(run(['modinfo', '-F', 'vermagic', str(path)]).stdout.split()[0] == VERSION,
                    '前摄私有模块版本不匹配。')
    return enabled



def camera_migration_enabled():
    units = ('tb710fu-macro-camera.service', 'tb710fu-flash-hardware.service', 'tb710fu-torch.service')
    return any(Path('/etc/systemd/system', unit).exists() for unit in units)


def selected_files(manifest):
    files = FILES | OPTIONAL_FILES
    if camera_migration_enabled():
        files |= manifest['camera_migration']
    return files


def check_loaded_dependencies(manifest, front_enabled):
    # Explicitly loaded private front modules are kept outside the public module tree.
    # Validate their imports against the new tree without creating autoload aliases.
    manual = manifest['private_front_helpers'] if front_enabled else {}
    camera_modules = {}
    if camera_migration_enabled():
        camera_modules = {Path(path).stem.replace('-', '_'): source for path, source
                          in manifest['camera_migration'].items() if path.endswith('.ko')}
    for line in Path('/proc/modules').read_text().splitlines():
        name = line.split()[0]
        filename = name + '.ko'
        if filename in manual:
            path = Path('/opt/tb710fu-front-camera') / VERSION / filename
            dependencies = run(['modinfo', '-F', 'depends', str(path)]).stdout.strip().split(',')
            dependencies = [value for value in dependencies if value]
        elif name in camera_modules:
            path = Path(__file__).resolve().parent / camera_modules[name]
            require(run(['modinfo', '-F', 'vermagic', str(path)]).stdout.split()[0] == VERSION,
                    '相机配套模块 ABI 不匹配：' + name)
            dependencies = [part for part in run(['modinfo', '-F', 'depends', str(path)]).stdout.strip().split(',')
                            if part and part.replace('-', '_') not in camera_modules]
        else:
            dependencies = [name]
        for dependency in dependencies:
            run(['modprobe', '--set-version', VERSION, '--show-depends', dependency])


def bundle(root):
    require(re.fullmatch('[0-9a-f]{64}', MANIFEST_SHA256),
            '这是开发源码，未绑定升级载荷。请使用完整 ubuntu-kernel-upgrade 离线包。')
    manifest_file = root / 'manifest.json'
    require(sha(manifest_file) == MANIFEST_SHA256, '升级包 manifest 校验失败。')
    manifest = json.loads(manifest_file.read_text())
    require(manifest['version'] == VERSION and manifest['format'] == 2 and manifest['release'] == RELEASE_PLAN, '不支持的升级包版本。')
    for name, expected in manifest['files'].items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts, '升级包路径异常。')
        source = root / name
        require(source.is_file() and not source.is_symlink(), '升级包缺少常规文件：' + name)
        require(sha(source) == expected, '升级包文件校验失败：' + name)
    require(manifest['files']['boot.img'] == BOOT_HASH, '启动镜像不是已验证版本。')
    require((root / 'boot.img').stat().st_size == BOOT_SIZE, '启动镜像大小异常。')
    return manifest


def environment():
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64', '仅支持平板上的 Ubuntu ARM64 Linux。')
    require(os.geteuid() == 0, '请用 sudo python3 upgrade-ubuntu-kernel.py ... 执行。')
    require(re.search(r'^ID=ubuntu$', Path('/etc/os-release').read_text(), re.M), '不是 Ubuntu 系统。')
    require(Path('/run/systemd/system').is_dir(), '需要完整 systemd 系统，不能在普通容器/chroot 内升级宿主机。')
    compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    require(b'lenovo,tb710fu' in compatible, '机型不是 TB710FU。')
    if platform.release() != VERSION:
        validate_source()
    for command in ('sfdisk', 'sgdisk', 'blockdev', 'findmnt', 'blkid', 'modinfo', 'modprobe', 'depmod', 'systemctl'):
        require(shutil.which(command), '缺少工具 ' + command + '；先安装 gdisk、fdisk、kmod、util-linux。')
    require('androidboot.slot_suffix=_a' in Path('/proc/cmdline').read_text().split(), '当前不是已验证的 A 槽启动。')


def disk_inventory(manifest):
    boot = Path('/dev/disk/by-partlabel/boot_a').resolve(strict=True)
    require(stat.S_ISBLK(boot.stat().st_mode), 'boot_a 不是块设备。')
    block = Path('/sys/class/block') / boot.name
    require((block / 'partition').is_file(), '无法动态定位 boot_a 所在磁盘。')
    disk = Path('/dev') / block.resolve().parent.name
    number = int((block / 'partition').read_text())
    tables = {}
    for entry in sorted(Path('/sys/class/block').glob('sd*')):
        if (entry / 'partition').exists():
            continue
        node = '/dev/' + entry.name
        check = run(['sgdisk', '-v', node])
        require('No problems found.' in check.stdout + check.stderr, 'GPT 主/备校验未通过：' + node)
        tables[node] = json.loads(run(['sfdisk', '--json', node]).stdout)
    require(normalized_gpt(tables) == manifest['gpt_layout'],
            '当前 GPT 布局或关键属性不在已验证范围。已允许 A/B 优先级、重试计数和成功状态的已知差异。')
    own = tables[str(disk)]['partitiontable']['partitions']
    found = [p for p in own if p.get('name') == 'boot_a']
    require(len(found) == 1 and Path(found[0]['node']).resolve() == boot, 'GPT boot_a 与设备标签不一致。')
    require(int(run(['blockdev', '--getsize64', str(boot)]).stdout) == BOOT_SIZE, 'boot_a 大小不匹配。')
    root_info = run(['findmnt', '-n', '-o', 'SOURCE,FSTYPE', '/']).stdout.split()
    require(len(root_info) == 2 and root_info[1] == 'ext4', '当前根文件系统不是已验证的 ext4。')
    root = Path(root_info[0]).resolve(strict=True)
    require(root == Path('/dev/disk/by-partlabel/arch').resolve(strict=True), '当前根分区不是启动镜像使用的 arch 标签。')
    root_uuid = run(['blkid', '-s', 'UUID', '-o', 'value', str(root)]).stdout.strip()
    firmware = {}
    for name, expected in manifest['firmware'].items():
        node = Path('/dev/disk/by-partlabel') / name
        require(node.exists(), '缺少固件分区：' + name)
        firmware[name] = sha(node)
        require(firmware[name] == expected, '当前固件与已验证版本不同：' + name)
    boot_hash = sha(boot)
    require(boot_hash in OLD_HASHES | {BOOT_HASH}, '当前启动镜像不是已验证的旧/新 Ubuntu 镜像。')
    flags = get_flags(disk, number)
    require(flags & (1 << 50) and not flags & (1 << 55), 'boot_a 未激活或已标记不可启动。')
    return {'boot': str(boot), 'boot_rdev': boot.stat().st_rdev, 'disk': str(disk), 'number': number,
            'boot_sha256': boot_hash, 'flags': flags, 'root_uuid': root_uuid, 'gpt': tables, 'firmware': firmware}


def get_flags(disk, number):
    info = run(['sgdisk', '-i', str(number), str(disk)]).stdout
    match = re.search(r'^Attribute flags:\s*([0-9A-Fa-f]+)$', info, re.M)
    require(match is not None, '无法读取当前启动属性。')
    return int(match[1], 16)


def set_flags(inventory, flags):
    # Change attributes only, never restore an old GPT or resize any partition.
    before = json.loads(run(['sfdisk', '--json', inventory['disk']]).stdout)
    run(['sgdisk', '--attributes=' + str(inventory['number']) + ':=:0x' + format(flags, '016x'), inventory['disk']])
    after = json.loads(run(['sfdisk', '--json', inventory['disk']]).stdout)
    require(get_flags(inventory['disk'], inventory['number']) == flags, '启动属性回读失败。')
    for wrapped in (before, after):
        target = next(p for p in wrapped['partitiontable']['partitions'] if p.get('name') == 'boot_a')
        target.pop('attrs', None)
    require(before == after, '启动属性以外的 GPT 字段发生变化，请停止操作并检查备份。')


def same_machine(before, now):
    require(before['root_uuid'] == now['root_uuid'], '根文件系统 UUID 已改变。')
    require(normalized_gpt(before['gpt'], identity=True) == normalized_gpt(now['gpt'], identity=True),
            'GPT 身份/布局已改变，拒绝使用此会话的备份。')
    require(before['firmware'] == now['firmware'], '固件发生变化。')


def write_image(source, inventory, expected_before, expected_after):
    """Guard the open device too; verify the complete read-back before success."""
    require(sha(source) == expected_after, '待写镜像校验失败。')
    with open(source, 'rb') as src:
        fd = os.open(inventory['boot'], os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r+b', buffering=0) as dst:
            require(os.fstat(dst.fileno()).st_rdev == inventory['boot_rdev'], '启动设备身份改变。')
            digest = hashlib.file_digest(dst, 'sha256').hexdigest()
            require(digest == expected_before, '启动分区在写入前发生变化。')
            dst.seek(0)
            shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
            os.fsync(dst.fileno())
            dst.seek(0)
            require(hashlib.file_digest(dst, 'sha256').hexdigest() == expected_after, '启动分区完整回读校验失败。')


def validate_archive(archive):
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
    total = 0
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        require(not path.is_absolute() and '..' not in path.parts and path.parts and path.parts[0] == VERSION,
                '模块归档包含越界路径。')
        require(member.isdir() or member.isfile(), '模块归档包含链接/设备/其他特殊文件。')
        require(str(path) not in seen, '模块归档包含重复路径。')
        seen.add(str(path))
        total += member.size
    require(0 < total < 512 * 1024 * 1024, '模块归档总大小异常。')
    return members


def extract_modules(archive, destination):
    members = validate_archive(archive)
    with tarfile.open(archive) as handle:
        for member in members:
            path = destination / member.name
            if member.isdir():
                path.mkdir(mode=0o755, parents=True, exist_ok=True)
            else:
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                with handle.extractfile(member) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target)
                path.chmod(0o644)
    for path in destination.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)


def backup_file(work, target, state):
    target = Path(target)
    require(not target.is_symlink(), '目标配置是符号链接，拒绝自动覆盖：' + str(target))
    record = {'existed': target.exists()}
    if target.exists():
        require(target.is_file() and target.stat().st_uid == 0, '目标配置不是 root 所有的常规文件：' + str(target))
        relative = str(target).lstrip('/')
        saved = work / 'files-before' / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
        record.update(mode=stat.S_IMODE(target.stat().st_mode), sha256=sha(saved), saved=relative)
    state['files_before'][str(target)] = record
    save_state(work, state)


def managed_file(work, target, data, mode, state):
    backup_file(work, target, state)
    state['files_before'][str(Path(target))]['after_sha256'] = hashlib.sha256(data).hexdigest()
    save_state(work, state)
    atomic(target, data, mode)


def validate_backups(work, state):
    if state.get('boot_write_started'):
        require(sha(work / 'boot-before.img') == state['before']['boot_sha256'], '原始启动镜像备份已损坏。')
    for name, record in state['files_before'].items():
        if record['existed']:
            saved = work / 'files-before' / record['saved']
            require(saved.is_file() and sha(saved) == record['sha256'], '配置备份校验失败：' + name)
        target = Path(name)
        require(not target.is_symlink(), '目标文件变成了符号链接：' + name)
        if target.exists():
            accepted = {record.get('sha256'), record.get('after_sha256')}
            require(target.is_file() and sha(target) in accepted, '目标文件已被其他操作修改，拒绝回退覆盖：' + name)
        else:
            require(not record['existed'], '原有目标文件被其他操作删除：' + name)


def restore_files(work, state):
    for name, record in reversed(list(state['files_before'].items())):
        target = Path(name)
        if record['existed']:
            saved = work / 'files-before' / record['saved']
            require(sha(saved) == record['sha256'], '配置备份校验失败：' + name)
            atomic(target, saved.read_bytes(), record['mode'])
        else:
            target.unlink(missing_ok=True)


def installation_preflight(bundle_root, manifest, before, user):
    validate_source()
    private_front_preflight(manifest)
    for name, expected in manifest['required_firmware_files'].items():
        require(Path(name).is_file() and sha(name) == expected, '后摄 overlay 与已验证版本不匹配：' + name)
    require(before['boot_sha256'] in OLD_HASHES, '运行旧内核但磁盘已存在新镜像；请先重启或检查上次升级状态。')
    previous_active = BASE.parent / '7.2.6-tb710fu-20261117/kernel-upgrade-active'
    if previous_active.is_symlink():
        previous = previous_active.resolve(strict=True)
        require(previous.parent == previous_active.parent.resolve(), '20261117 升级记录路径异常。')
        phase = json.loads((previous / 'state.json').read_text())['phase']
        require(phase in ('kernel-validated', 'complete'), '20261117 升级尚未完成，请先检查旧升级状态。')
    require(not ACTIVE.exists() and not ACTIVE.is_symlink(), '存在上次升级记录；先查看 --status，必要时 --rollback。')
    require(not UNIT_PATH.exists() and not UNIT_PATH.is_symlink(), '升级服务已存在，请先检查。')
    require(not Path('/lib/modules', VERSION).exists(), '目标模块目录已存在，拒绝覆盖未知/未完成的模块安装。')
    require(not Path('/opt/tb710fu-camera-test', VERSION).exists(), '目标后摄目录已存在，请先检查上次升级。')
    require(shutil.disk_usage('/').free >= 1024**3, '根分区至少需要 1 GiB 可用空间。')
    batteries = list(Path('/sys/class/power_supply').glob('*/capacity'))
    require(batteries and min(int(p.read_text()) for p in batteries) >= 30, '需要可读取的电池电量，且至少 30%。')
    account = pwd.getpwnam(user)
    require(account.pw_uid != 0 and Path(account.pw_dir).is_dir(), '--user 必须是已有主目录的普通用户。')
    chosen = selected_files(manifest)
    for target, accepted in manifest['previous_files'].items():
        if target not in chosen:
            continue
        path = Path(target)
        if not path.exists() and not path.is_symlink() and target not in {
                '/opt/tb710fu-camera-test/tb-camera-live.sh', '/opt/tb710fu-camera-test/tb-camera-focus.sh',
                '/usr/local/sbin/tb-ubuntu-usb-ncm'}:
            continue
        require(path.is_file() and not path.is_symlink() and sha(path) in accepted,
                '旧系统配套脚本与已验证基线不一致：' + target)
    for target, source in chosen.items():
        if target in OPTIONAL_FILES and not Path(target).exists():
            continue
        path = Path(target)
        if str(path) not in manifest['previous_files'] and path.exists():
            require(path.is_file() and not path.is_symlink() and sha(path) == manifest['files'][source],
                    '目标配置已存在自定义内容，拒绝覆盖：' + target)
    if camera_migration_enabled():
        require(run(['systemctl', 'is-enabled', 'tb710fu-front-camera.service'], check=False).returncode == 0,
                '已装的微距/闪光灯配套需要前摄服务及目标 ABI 模块。')
    for service in ('tb710fu-camera-test', 'tb710fu-camera-focus', 'tb-ubuntu-usb-ncm'):
        require(run(['systemctl', 'is-enabled', service], check=False).returncode == 0,
                '旧系统配套服务未启用：' + service)
    usb_config = Path('/etc/tb710fu/usb-ncm-host-address')
    if usb_config.exists():
        require(not usb_config.is_symlink() and usb_config.stat().st_uid == 0 and
                stat.S_IMODE(usb_config.stat().st_mode) == 0o600, '已有 USB 身份文件必须为 root 所有、0600 权限。')
    usb_address = (usb_config if usb_config.exists() else Path('/sys/kernel/config/usb_gadget/g1/functions/ncm.usb0/host_addr')).read_text().strip().lower()
    require(re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', usb_address), '不能读取有效 USB NCM 身份，未进行写入。')
    raw_mac = bytes.fromhex(usb_address.replace(':', ''))
    require(not raw_mac[0] & 1 and any(raw_mac), 'USB NCM 身份不是有效单播地址。')
    validate_archive(bundle_root / 'modules.tar')
    return usb_address


def install(bundle_root, manifest, before, user, reboot):
    if platform.release() == VERSION:
        require(before['boot_sha256'] == BOOT_HASH, '运行内核已更新，但启动分区不同；请先检查。')
        log('当前已经运行目标内核，未写入任何分区。')
        return
    usb_address = installation_preflight(bundle_root, manifest, before, user)
    chosen = selected_files(manifest)
    usb_config = Path('/etc/tb710fu/usb-ncm-host-address')
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=time.strftime('kernel-upgrade-%Y%m%d-%H%M%S-'), dir=BASE))
    work.chmod(0o700)
    payload = work / 'payload'
    payload.mkdir(mode=0o700)
    for name in list(manifest['files']) + ['manifest.json', 'upgrade-ubuntu-kernel.py']:
        shutil.copy2(bundle_root / name, payload / name)
    require(sha(payload / 'upgrade-ubuntu-kernel.py') == sha(Path(__file__)), '私有副本的升级器校验失败。')
    bundle(payload)
    state = {'phase': 'preparing', 'user': user, 'before': before, 'files_before': {}, 'boot_write_started': False,
             'apt_holds': run(['apt-mark', 'showhold']).stdout,
             'source_version': platform.release(), 'front_enabled': private_front_preflight(manifest),
             'camera_migrated': camera_migration_enabled()}
    save_state(work, state)
    try:
        # Record fresh GPT copies from every current UFS LUN, not historical coordinates.
        for disk in before['gpt']:
            stem = Path(disk).name
            run(['sgdisk', '--backup=' + str(work / (stem + '.gpt')), disk])
            atomic(work / (stem + '.sfdisk'), run(['sfdisk', '--dump', disk]).stdout.encode())
        shutil.copyfile(before['boot'], work / 'boot-before.img')
        require(sha(work / 'boot-before.img') == before['boot_sha256'], '旧启动镜像备份校验失败。')
        stage = work / 'modules-stage'
        stage.mkdir()
        extract_modules(payload / 'modules.tar', stage)
        new_modules = stage / VERSION
        extra = new_modules / 'extra'
        extra.mkdir(exist_ok=True)
        shutil.copy2(payload / 'tb_dp_overlay.ko', extra / 'tb_dp_overlay.ko')
        for name in HELPERS:
            shutil.copy2(payload / name, extra / name)
        modules = list(new_modules.rglob('*.ko'))
        require(modules, '模块集合为空。')
        for offset in range(0, len(modules), 64):
            versions = run(['modinfo', '-F', 'vermagic'] + [str(p) for p in modules[offset:offset+64]]).stdout.splitlines()
            require(len(versions) == len(modules[offset:offset+64]) and all(v.split()[0] == VERSION for v in versions),
                    '模块版本不匹配，未写入启动分区。')
        os.rename(new_modules, Path('/lib/modules') / VERSION)
        state['modules_installed'] = True
        save_state(work, state)
        depmod()
        check_loaded_dependencies(manifest, state['front_enabled'])
        camera = Path('/opt/tb710fu-camera-test') / VERSION
        camera.mkdir(mode=0o755)
        camera.chmod(0o755)
        state['camera_installed'] = True
        save_state(work, state)
        for name in HELPERS + ('ov13b10-rggb-UNTESTED.ko', 'qcom-camss-graph-UNTESTED.ko'):
            shutil.copy2(payload / name, camera / name)
            (camera / name).chmod(0o644)
        for target, source in chosen.items():
            if target in OPTIONAL_FILES and not Path(target).exists():
                continue
            managed_file(work, target, (payload / source).read_bytes(), 0o755 if source in EXECUTABLES or target.startswith(
                             ('/usr/local/bin/', '/usr/local/sbin/', '/usr/local/libexec/')) else 0o644, state)
        if not usb_config.exists():
            usb_config.parent.mkdir(mode=0o700, exist_ok=True)
            managed_file(work, usb_config, (usb_address + '\n').encode(), 0o600, state)
        unit = ('[Unit]\nDescription=Complete TB710FU dated kernel upgrade\n'
                'After=network-online.target tb710fu-camera-test.service tb710fu-camera-focus.service tb710fu-front-camera.service tb-ubuntu-usb-ncm.service\n'
                'Wants=network-online.target\n'
                '[Service]\nType=oneshot\nTimeoutStartSec=7200\n'
                'ExecStart=/usr/bin/python3 ' + str(ACTIVE / 'payload/upgrade-ubuntu-kernel.py') + ' --finish\n'
                '[Install]\nWantedBy=multi-user.target\n')
        managed_file(work, UNIT_PATH, unit.encode(), 0o644, state)
        ACTIVE.symlink_to(work, target_is_directory=True)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', UNIT])
        private_front_preflight(manifest)
        now = disk_inventory(manifest)
        same_machine(before, now)
        require(now['boot_sha256'] == before['boot_sha256'], '备份后启动分区发生变化。')
        state['phase'] = 'writing-boot'
        state['boot_write_started'] = True
        save_state(work, state)
        trial_flags = (now['flags'] & ~TRIAL_MASK) | (7 << 51)
        set_flags(now, trial_flags)
        os.sync()
        log('备份、模块检查完成，写入已验证的启动镜像……')
        write_image(payload / 'boot.img', now, before['boot_sha256'], BOOT_HASH)
        state['phase'] = 'awaiting-reboot'
        save_state(work, state)
        os.sync()
    except BaseException:
        log('升级未完成，尝试恢复本次启动镜像/属性及配套文件；备份：' + str(work))
        rollback(work, state, manifest, failure=True)
        raise
    log('已写入并完整回读校验。旧内核继续运行，重启后自动完成 Snap 修复。备份：' + str(work))
    if reboot:
        run(['systemctl', 'reboot'])
    else:
        log('准备好后执行 sudo reboot；当前未自动重启。')


def finish(work, state, manifest):
    require(state['phase'] in ('writing-boot', 'awaiting-reboot', 'kernel-validated', 'complete'), '当前状态不能执行收尾：' + state['phase'])
    require(platform.release() == VERSION, '尚未进入新内核，请重启后再运行 --finish。')
    now = disk_inventory(manifest)
    same_machine(state['before'], now)
    require(now['boot_sha256'] == BOOT_HASH, '启动分区不是本次已验证镜像。')
    for service in ('tb710fu-camera-test', 'tb710fu-camera-focus', 'tb-ubuntu-usb-ncm'):
        require(run(['systemctl', 'is-active', service], check=False).returncode == 0, '新内核配套服务未运行：' + service)
    if state.get('front_enabled'):
        require(run(['systemctl', 'is-active', 'tb710fu-front-camera'], check=False).returncode == 0,
                '原本启用的前摄服务未运行，不能标记完成。')
    require(any(p.read_text().strip() == 'connected' for p in Path('/sys/class/drm').glob('card*-DSI-*/status')),
            '内屏 DSI 未就绪，不能标记启动成功。')
    if state.get('camera_migrated'):
        for service in ('tb710fu-macro-camera', 'tb710fu-flash-hardware', 'tb710fu-torch'):
            if run(['systemctl', 'is-enabled', service], check=False).returncode == 0:
                require(run(['systemctl', 'is-active', service], check=False).returncode == 0,
                        '原本启用的配套服务未运行：' + service)
    repair = work / 'payload/repair-ubuntu-snap.py'
    result = run([sys.executable, str(repair), '--apply'], check=False, timeout=2400)
    atomic(work / 'snap-repair.log', (result.stdout + result.stderr).encode())
    require(result.returncode == 0, '新内核已启动，但 Snap 配置未完成；详见 ' + str(work / 'snap-repair.log'))
    # A network-dependent hello-world download must not consume a healthy kernel's boot trials.
    now = disk_inventory(manifest)
    same_machine(state['before'], now)
    set_flags(now, now['flags'] | (1 << 54))
    state['phase'] = 'kernel-validated'
    save_state(work, state)
    result = run([sys.executable, str(repair), '--apply', '--verify', '--user', state['user']], check=False, timeout=2400)
    atomic(work / 'snap-verify.log', (result.stdout + result.stderr).encode())
    require(result.returncode == 0, '内核和 Snap strict/full 已验证；完整隔离测试待完成，请联网后重试 --finish。日志：' + str(work / 'snap-verify.log'))
    require(run(['apt-mark', 'showhold']).stdout == state['apt_holds'], '更新保护列表已变化，请检查。')
    state['phase'] = 'complete'
    save_state(work, state)
    run(['systemctl', 'disable', UNIT])
    log('完成：新内核、Snap strict/full 和普通用户实际隔离测试通过。')


def rollback(work, state, manifest, *, failure=False):
    validate_backups(work, state)
    now = disk_inventory(manifest) if not failure or not state.get('boot_write_started') else None
    if now is not None:
        same_machine(state['before'], now)
    if state.get('boot_write_started'):
        # A failed write can leave a partial image: inspect GPT/firmware using the known backup
        # separately, never accept a different device or a changed partition layout.
        if now is None:
            now = copy.deepcopy(state['before'])
            for disk in now['gpt']:
                now['gpt'][disk] = json.loads(run(['sfdisk', '--json', disk]).stdout)
            now['firmware'] = {name: sha(Path('/dev/disk/by-partlabel') / name) for name in manifest['firmware']}
            same_machine(state['before'], now)
            require(Path(now['boot']).stat().st_rdev == now['boot_rdev'], '启动设备身份改变，停止自动恢复。')
        old = work / 'boot-before.img'
        require(sha(old) == state['before']['boot_sha256'], '原始启动镜像备份已损坏。')
        current_hash = sha(now['boot'])
        if not failure:
            require(current_hash in (BOOT_HASH, state['before']['boot_sha256']), '启动分区已被其他操作更改，拒绝回退。')
        write_image(old, now, current_hash, state['before']['boot_sha256'])
        current_flags = get_flags(now['disk'], now['number'])
        set_flags(now, (current_flags & ~TRIAL_MASK) | (state['before']['flags'] & TRIAL_MASK))
    run(['systemctl', 'disable', UNIT], check=False)
    restore_files(work, state)
    if ACTIVE.is_symlink() and ACTIVE.resolve() == work.resolve():
        ACTIVE.unlink()
    run(['systemctl', 'daemon-reload'])
    # Never remove a module tree belonging to the currently running kernel.
    if platform.release() != VERSION:
        if state.get('modules_installed'):
            shutil.rmtree(Path('/lib/modules') / VERSION)
        if state.get('camera_installed'):
            shutil.rmtree(Path('/opt/tb710fu-camera-test') / VERSION)
    state['phase'] = 'rolled-back'
    save_state(work, state)
    os.sync()
    log('旧启动镜像和配套文件已恢复；备份保留。新内核若仍在运行，需手动重启切回旧内核。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ('check', 'install', 'finish', 'rollback', 'status'):
        mode.add_argument('--' + name, action='store_true')
    parser.add_argument('--user', default='ubuntu', help='普通桌面用户，默认 ubuntu')
    parser.add_argument('--reboot', action='store_true', help='仅 --install 成功后自动重启')
    args = parser.parse_args()
    require(not args.reboot or args.install, '--reboot 只能与 --install 一起使用。')
    os.umask(0o077)
    os.environ['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['LC_ALL'] = 'C.UTF-8'
    environment()
    root = Path(__file__).resolve().parent
    if args.finish or args.rollback or args.status:
        require(ACTIVE.is_symlink(), '没有活动的内核升级记录。')
        work = ACTIVE.resolve(strict=True)
        require(work.parent == BASE.resolve(), '活动记录路径不安全。')
        manifest = bundle(work / 'payload')
        state = json.loads((work / 'state.json').read_text())
        if args.status:
            log('升级状态：' + state['phase'] + '\n运行内核：' + platform.release() + '\n备份：' + str(work))
            return
    else:
        manifest = bundle(root)
        before = disk_inventory(manifest)
        validate_archive(root / 'modules.tar')
        if args.check:
            if platform.release() != VERSION:
                installation_preflight(root, manifest, before, args.user)
            log('机型、运行环境、固件、GPT、启动镜像和升级包校验通过。')
            log('运行内核：' + platform.release() + ('；已是目标版本，无需重复刷写。' if platform.release() == VERSION else '；可执行 --install。'))
            return
    with open('/run/lock/tb710fu-kernel-upgrade.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('另一个升级/收尾进程正在运行。')
        if args.install:
            install(root, manifest, before, args.user, args.reboot)
        elif args.finish:
            finish(work, state, manifest)
        elif args.rollback:
            rollback(work, state, manifest)


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
        print('停止：' + str(exc), file=sys.stderr)
        sys.exit(1)
