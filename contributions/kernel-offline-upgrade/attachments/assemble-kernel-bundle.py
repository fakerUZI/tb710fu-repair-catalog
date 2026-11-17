#!/usr/bin/env python3
"""Verify and reconstruct catalog attachments, then invoke the reviewed upgrader.

No network download, eval, automatic reboot or unchecked extraction is used.
--action validate verifies the bundle without accessing devices or running repair.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def extract(archive, destination, expected_root):
    with tarfile.open(archive, 'r:gz') as handle:
        members = handle.getmembers()
        seen, total = set(), 0
        for member in members:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and name.parts and name.parts[0] == expected_root and
                    '..' not in name.parts and '\\' not in member.name, '归档路径不安全。')
            require(member.isfile() or member.isdir(), '归档含链接或特殊文件。')
            require(str(name) not in seen, '归档包含重复路径。')
            seen.add(str(name))
            total += member.size
            require(member.size <= 128 * 1024 * 1024 and total <= 256 * 1024 * 1024,
                    '归档展开尺寸异常。')
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)


def policy_module():
    loader = importlib.util.spec_from_file_location('tb_kernel_release', Path(__file__).with_name('kernel-release.py'))
    policy = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(policy)
    return policy


def check_release(spec, module, action, current=None):
    """Bind the actual installer to the same version policy as the catalog entry."""
    require(spec['format'] == 2, '历史升级包已经移除，仅支持日期版升级包。')
    plan = spec['release']
    policy = policy_module()
    policy.validate(plan)
    require(module.RELEASE_PLAN == plan and module.VERSION == plan['target_version'],
            '升级器的版本策略与分片清单不符。')
    if action in ('install', 'check'):
        policy.validate(plan, platform.release() if current is None else current)


def environment():
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64', '仅支持 TB710FU Ubuntu ARM64。')
    require('ID=ubuntu' in Path('/etc/os-release').read_text().splitlines(), '仅支持 Ubuntu。')
    compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    require(b'lenovo,tb710fu' in compatible, '机型不是 TB710FU。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attachments-dir', type=Path, required=True)
    parser.add_argument('--action', choices=['install', 'check', 'validate', 'status', 'finish', 'rollback'], default='check')
    parser.add_argument('--user', default='ubuntu')
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), '需要 Python 3.11 或更新版本。')
    require(args.action in ('validate', 'check') or os.geteuid() == 0, '需要管理员权限。')
    os.umask(0o077)
    attachments = args.attachments_dir.resolve(strict=True)
    spec = json.loads((attachments / 'bundle-parts.json').read_text())
    require(spec['format'] == 2, '历史升级包已移除，仅支持日期版升级包。')
    plan = json.loads((attachments / 'kernel-release.json').read_text())
    require(spec['release'] == plan, '发布计划与载荷版本策略不一致。')
    policy_module().validate(plan)
    if args.action in ('install', 'check'):
        environment()
        policy_module().validate(plan, platform.release())
    if spec.get('payload_status') == 'not-built':
        require(spec['parts'] == [], '未构建状态不能包含分片。')
        if args.action == 'check':
            print('版本资格通过：' + platform.release() + ' -> ' + plan['target_version'])
            print('日期版载荷尚未构建，不能安装；未进行固件/GPT/镜像预检，未写入设备。')
            return 0
        raise ValueError('日期版载荷尚未构建；已移除历史 snap1 分片，不能执行此动作。')
    require(spec.get('payload_status') == 'ready' and 0 < len(spec['parts']) <= 20, '缺少完整的已审核日期版载荷。')
    require(shutil.disk_usage('/var/tmp').free >= 512 * 1024 * 1024, '/var/tmp 至少需要 512 MiB 可用空间。')
    with tempfile.TemporaryDirectory(prefix='tb710fu-snap-bundle-', dir='/var/tmp') as temp:
        temp = Path(temp)
        archive = temp / 'bundle.tar.gz'
        with archive.open('xb') as output:
            for part in spec['parts']:
                name = part['name']
                require(Path(name).name == name and name not in ('.', '..'), '分片文件名不安全。')
                path = attachments / name
                require(path.is_file() and not path.is_symlink(), '缺少分片：' + name)
                require(path.stat().st_size == part['size'] <= 10 * 1024 * 1024,
                        '分片大小不符：' + name)
                require(digest(path) == part['sha256'], '分片校验失败：' + name)
                with path.open('rb') as source:
                    shutil.copyfileobj(source, output)
        require(digest(archive) == spec['archive_sha256'], '完整压缩包校验失败。')
        extract(archive, temp, spec['root'])
        root = temp / spec['root']
        installer = root / 'upgrade-ubuntu-kernel.py'
        require(digest(installer) == spec['installer_sha256'], '升级器校验失败。')
        require(digest(root / 'manifest.json') == spec['manifest_sha256'], '载荷清单校验失败。')
        loader = importlib.util.spec_from_file_location('reviewed_tb710fu_upgrade', installer)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        check_release(spec, module, args.action)
        module.bundle(root)
        module.validate_archive(root / 'modules.tar')
        if args.action == 'validate':
            print('PASS: 分片、压缩包、升级器、完整载荷及模块归档校验通过；未执行设备操作。')
            return 0
        result = subprocess.run([sys.executable, str(installer), '--' + args.action, '--user', args.user])
        return result.returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print('停止：' + str(exc), file=sys.stderr)
        sys.exit(1)
