#!/usr/bin/env python3
"""Install/rollback a pinned snap1 network module repair; never writes boot.

Build replaces MANIFEST_SHA256. Default action is read-only check. Module
files are updated atomically; a reboot activates the repaired dependencies.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

VERSION = '7.2.6-tb710fu-snap1'
MANIFEST_SHA256 = 'BUILD_TIME_MANIFEST_SHA256'
BACKUP = Path('/var/backups/tb710fu-network-repair-20261117')
LOAD_FILE = Path('/etc/modules-load.d/90-tb710fu-network-repair.conf')
ROOT = Path('/lib/modules') / VERSION
ALLOWED = ('kernel/net/netfilter/', 'kernel/net/ipv4/netfilter/',
           'kernel/net/ipv6/netfilter/', 'kernel/drivers/net/wireless/ath/ath12k/')
INDEXES = ('modules.dep', 'modules.dep.bin', 'modules.alias', 'modules.alias.bin',
           'modules.softdep', 'modules.weakdep', 'modules.symbols', 'modules.symbols.bin',
           'modules.devname', 'modules.builtin.bin', 'modules.builtin.alias.bin')


def allowed_before(entry):
    return [None, entry['sha256'], entry['before_sha256'], *entry.get('accepted_before_sha256', [])]


def depmod():
    previous = os.umask(0o022)
    try:
        subprocess.run(['depmod', VERSION], check=True)
        for name in INDEXES:
            p = ROOT/name
            if p.is_file() and not p.is_symlink():
                p.chmod(0o644)
    finally:
        os.umask(previous)


def digest(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def replace(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.tb-network-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o644)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def target_checks(manifest):
    if os.geteuid() != 0 or os.uname().machine != 'aarch64' or os.uname().release != VERSION:
        raise RuntimeError('Requires root on the exact aarch64 snap1 kernel')
    model = Path('/sys/firmware/devicetree/base/model').read_bytes()
    if b'TB710FU' not in model:
        raise RuntimeError('Device model is not TB710FU')
    config = subprocess.check_output(['zcat', '/proc/config.gz'])
    if hashlib.sha256(config).hexdigest() != manifest['running_config_sha256']:
        raise RuntimeError('Running kernel configuration does not match the ABI baseline')


def validate(base, manifest):
    for rel, entry in manifest['modules'].items():
        p = Path(rel)
        if p.is_absolute() or '..' in p.parts or not rel.endswith('.ko') or not rel.startswith(ALLOWED):
            raise RuntimeError('Unexpected module path: '+rel)
        dst = ROOT / p
        if dst.is_symlink() or not dst.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError('Unsafe module destination: '+rel)
        src = base/'modules'/p
        if src.is_symlink() or not src.is_file() or digest(src) != entry['sha256']:
            raise RuntimeError('Package module hash mismatch: '+rel)
        vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(src)], text=True)
        if vermagic.split()[0] != VERSION:
            raise RuntimeError('Module kernel version mismatch: '+rel)
    if digest(base/'modules-load.conf') != manifest['modules_load_sha256']:
        raise RuntimeError('Preload configuration hash mismatch')


def backup_state(manifest):
    state = json.loads((BACKUP/'state.json').read_text())
    if set(state['before']) != set(manifest['modules']):
        raise RuntimeError('Backup module inventory differs from reviewed package')
    # Validate the entire backup before changing even the first destination.
    for rel, old in state['before'].items():
        entry = manifest['modules'][rel]
        if old not in allowed_before(entry):
            raise RuntimeError('Unexpected backup fingerprint: '+rel)
        if old is not None:
            src = BACKUP/'modules'/rel
            if src.is_symlink() or digest(src) != old:
                raise RuntimeError('Backup hash mismatch: '+rel)
    if state['load_before'] is not None:
        if digest(BACKUP/'modules-load.conf') != state['load_before']:
            raise RuntimeError('Preload backup hash mismatch')
    return state


def restore(manifest):
    state = backup_state(manifest)
    for rel, old in state['before'].items():
        dst = ROOT/rel
        if old is None:
            dst.unlink(missing_ok=True)
        else:
            src = BACKUP/'modules'/rel
            if digest(src) != old:
                raise RuntimeError('Backup hash mismatch: '+rel)
            replace(src, dst)
    if state['load_before'] is None:
        LOAD_FILE.unlink(missing_ok=True)
    else:
        src = BACKUP/'modules-load.conf'
        if digest(src) != state['load_before']:
            raise RuntimeError('Preload backup hash mismatch')
        replace(src, LOAD_FILE)
    depmod()
    os.sync()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['check', 'apply', 'rollback'], default='check', nargs='?')
    args = p.parse_args()
    base = Path(__file__).resolve().parent
    if digest(base/'manifest.json') != MANIFEST_SHA256:
        raise SystemExit('Manifest differs from the reviewed build')
    manifest = json.loads((base/'manifest.json').read_text())
    target_checks(manifest)
    validate(base, manifest)
    before = {}
    installed = True
    for rel, entry in manifest['modules'].items():
        dst = ROOT/rel
        value = digest(dst) if dst.exists() else None
        before[rel] = value
        installed &= value == entry['sha256']
        if value not in allowed_before(entry):
            raise SystemExit('Unknown existing module; refusing to overwrite: '+rel)
        if args.action == 'apply' and value is None and entry['before_sha256'] is not None:
            raise SystemExit('Expected baseline module is missing: '+rel)
    load_before = digest(LOAD_FILE) if LOAD_FILE.exists() else None
    if LOAD_FILE.is_symlink():
        raise SystemExit('Preload configuration cannot be a symlink')
    print('Target/ABI/package hashes verified; module files installed:', installed)
    marker = Path('/sys/module/ath12k/parameters/tb_vdev_count_fix')
    print('Active Wi-Fi backport revision:', marker.read_text().strip() if marker.exists() else 'not active')
    if args.action == 'check':
        return
    if args.action == 'rollback':
        restore(manifest)
        print('Original module files restored. Reboot is required; boot partitions were not written.')
        return
    if installed and load_before == manifest['modules_load_sha256']:
        print('Already installed; existing backup retained.')
        return
    if BACKUP.is_symlink():
        raise SystemExit('Backup directory cannot be a symlink')
    if BACKUP.exists():
        # A reviewed package revision can update a prior revision while keeping
        # the very first pre-repair snapshot immutable.
        backup_state(manifest)
    else:
        os.umask(0o077)
        BACKUP.mkdir(parents=True, mode=0o700)
        for rel, value in before.items():
            if value is not None:
                dst = BACKUP/'modules'/rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/rel, dst)
                if digest(dst) != value:
                    raise SystemExit('Backup verification failed: '+rel)
        if load_before:
            shutil.copyfile(LOAD_FILE, BACKUP/'modules-load.conf')
        (BACKUP/'state.json').write_text(json.dumps({'before': before, 'load_before': load_before}, indent=2))
        shutil.copyfile(base/'manifest.json', BACKUP/'manifest.json')
    try:
        for rel in manifest['modules']:
            replace(base/'modules'/rel, ROOT/rel)
            if digest(ROOT/rel) != manifest['modules'][rel]['sha256']:
                raise RuntimeError('Installed module readback mismatch: '+rel)
        replace(base/'modules-load.conf', LOAD_FILE)
        depmod()
        for name in manifest['required_modules']:
            subprocess.run(['modprobe', '--set-version', VERSION, '--show-depends', name],
                           check=True, stdout=subprocess.DEVNULL)
        os.sync()
    except BaseException:
        restore(manifest)
        raise
    print('Module files installed and verified. Reboot required; boot partitions were not written.')


if __name__ == '__main__':
    main()
