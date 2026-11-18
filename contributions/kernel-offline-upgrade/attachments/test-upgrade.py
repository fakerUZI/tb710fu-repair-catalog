#!/usr/bin/env python3
"""Integration tests: real bundle/files/image IO; simulated hardware and services.

Run as root in a disposable Ubuntu container. No host devices are used.
"""
import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BUNDLE = Path(os.environ['TB_UPGRADE_TEST_BUNDLE']).resolve()
OLD_BOOT = Path(os.environ['TB_UPGRADE_TEST_OLD_BOOT']).resolve()
SPEC = importlib.util.spec_from_file_location('upgrader', BUNDLE / 'upgrade-ubuntu-kernel.py')
u = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(u)
REAL_SHA = u.sha
REAL_PATH = Path
TEST_OLD_VERSION = os.environ.get("TB_UPGRADE_TEST_SOURCE", "7.2.6-tb710fu-snap1")


def flag_text(value):
    return 'GUID:' + ','.join(str(i) for i in range(48, 64) if value & (1 << i))


class Hardware:
    def __init__(self, base, manifest):
        self.base, self.manifest = base, manifest
        self.kernel = TEST_OLD_VERSION
        self.tables = {}
        # Use realistic approved geometry, with private fixture identities.
        for index, table in enumerate(manifest['gpt_layout']):
            table = copy.deepcopy(table)
            disk = '/dev/sd' + chr(97 + index)
            table.update(device=disk, id='fixture-disk-' + str(index))
            for number, part in enumerate(table['partitions'], 1):
                part.update(node=disk + str(number), uuid='fixture-part-' + str(index) + '-' + str(number))
                if part.get('name') == 'boot_a':
                    self.boot_disk, self.number = disk, number
                    self.partition = part
            self.tables[disk] = {'partitiontable': table}
        self.flags = (1 << 48) | (1 << 49) | (1 << 50) | (2 << 51) | (1 << 54)
        self.partition['attrs'] = flag_text(self.flags)
        self.boot = base / 'fake-boot-block.img'
        shutil.copyfile(OLD_BOOT, self.boot)
        self.services = []
        self.repairs = []
        self.firmware_changed = False
        self.verify_failure = False
        self.front_enabled = False

    def path(self, *args):
        path = REAL_PATH(*args)
        prefixes = ('/opt/', '/etc/', '/lib/modules/', '/lib/firmware/', '/usr/local/', '/sys/', '/proc/', '/dev/')
        if str(path).startswith(prefixes) or str(path) in tuple(p.rstrip('/') for p in prefixes):
            return self.base / 'root' / str(path).lstrip('/')
        return path

    def sha(self, path):
        path = REAL_PATH(path)
        firmware_prefix = self.base / 'root/dev/disk/by-partlabel'
        if path.parent == firmware_prefix and path.name in self.manifest['firmware']:
            return '0'*64 if self.firmware_changed else self.manifest['firmware'][path.name]
        return REAL_SHA(path)

    def inventory(self, manifest):
        return {'boot': str(self.boot), 'boot_rdev': self.boot.stat().st_rdev,
                'disk': self.boot_disk, 'number': self.number,
                'boot_sha256': REAL_SHA(self.boot), 'flags': self.flags,
                'root_uuid': 'fixture-root-uuid', 'gpt': copy.deepcopy(self.tables),
                'firmware': dict(manifest['firmware'])}

    def run(self, args, **options):
        args = list(map(str, args))
        out, error, rc = '', '', 0
        if args[0] == 'apt-mark':
            out = 'protected-kernel\nprotected-firmware\n'
        elif args[0] == 'sfdisk':
            out = json.dumps(self.tables[args[-1]])
        elif args[0] == 'sgdisk':
            if args[1] == '-i':
                out = 'Attribute flags: ' + format(self.flags, '016X') + '\n'
            elif args[1].startswith('--attributes='):
                self.flags = int(args[1].rsplit(':', 1)[1], 16)
                self.partition['attrs'] = flag_text(self.flags)
            elif args[1].startswith('--backup='):
                REAL_PATH(args[1].split('=',1)[1]).write_text(json.dumps(self.tables[args[-1]]))
            else:
                raise AssertionError(args)
        elif args[0] == 'systemctl':
            self.services.append(args[1:])
            if args[1:]==['is-enabled','tb710fu-front-camera.service']:
                rc = 0 if self.front_enabled else 1
        elif args[0] == 'modinfo':
            values = []
            for name in args[3:]:
                blob = REAL_PATH(name).read_bytes()
                assert blob[:4] == b'\x7fELF' and int.from_bytes(blob[18:20], 'little') == 183
                field = b'vermagic' if args[2] == 'vermagic' else b'depends'
                values.append(re.search(field + rb'=([^\0]*)', blob)[1].decode())
            out = '\n'.join(values) + '\n'
        elif args[0] == 'depmod':
            actual = subprocess.run(['depmod', '-b', str(self.base/'root'), u.VERSION], text=True, capture_output=True)
            out, error, rc = actual.stdout, actual.stderr, actual.returncode
        elif args[0] == 'modprobe':
            actual = subprocess.run(['modprobe', '-d', str(self.base/'root')] + args[1:], text=True, capture_output=True)
            out, error, rc = actual.stdout, actual.stderr, actual.returncode
        elif args[0] == sys.executable and args[1].endswith('repair-ubuntu-snap.py'):
            self.repairs.append(args[2:])
            out = 'simulated Snap repair subprocess success\n'
            if '--verify' in args and self.verify_failure:
                rc, error = 1, 'simulated unavailable store'
        else:
            raise AssertionError('Unexpected system command: ' + repr(args))
        result = subprocess.CompletedProcess(args, rc, out, error)
        if options.get('check', True) and rc:
            raise u.Stop(error)
        return result


class UpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0:
            raise RuntimeError('Run these integration tests as root inside a disposable container')
        cls.manifest = u.bundle(BUNDLE)

    def setup_machine(self, root):
        hardware = Hardware(root, self.manifest)
        for dest, source in {
            '/opt/tb710fu-camera-test/tb-camera-live.sh': BUNDLE / 'tb-camera-live.sh',
            '/opt/tb710fu-camera-test/tb-camera-focus.sh': BUNDLE / 'tb-camera-focus.sh',
            '/usr/local/sbin/tb-ubuntu-usb-ncm': BUNDLE / 'tb-ubuntu-usb-ncm',
            '/etc/modprobe.d/91-tb710fu-camera-manual.conf': BUNDLE / '91-tb710fu-camera-manual.conf',
            '/opt/tb710fu-front-camera/tb-front-camera.sh': BUNDLE / 'tb-front-camera.sh',
        }.items():
            target = hardware.path(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for dest, data in {
            '/sys/class/power_supply/battery/capacity': '90\n',
            '/sys/kernel/config/usb_gadget/g1/functions/ncm.usb0/host_addr': '02:00:00:00:00:01\n',
            '/proc/modules': 'libcomposite 100 0 - Live 0x0\n',
            '/sys/class/drm/card1-DSI-1/status': 'connected\n',
            '/lib/modules/' + TEST_OLD_VERSION + '/keep': 'old modules retained\n',
        }.items():
            target = hardware.path(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data)
        for name in self.manifest['required_firmware_files']:
            original = Path(os.environ['TB_UPGRADE_TEST_REAR_OVERLAY'])
            dest = hardware.path(name);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,dest)
        original = Path(os.environ['TB_UPGRADE_TEST_FRONT_OVERLAY'])
        dest = hardware.path('/lib/firmware/tb-front-raw.dtbo');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,dest)
        (root / 'home').mkdir()
        (root / 'private').mkdir()
        return hardware

    def mocks(self, hardware):
        from contextlib import ExitStack
        stack = ExitStack()
        for name, value in {
            'BASE': hardware.base / 'private',
            'ACTIVE': hardware.base / 'private/active',
            'UNIT_PATH': hardware.path('/etc/systemd/system/' + u.UNIT),
            'Path': hardware.path, 'run': hardware.run, 'disk_inventory': hardware.inventory,
            'sha': hardware.sha, '__file__': str(BUNDLE / 'upgrade-ubuntu-kernel.py'),
        }.items():
            stack.enter_context(patch.object(u, name, value))
        stack.enter_context(patch.object(u.platform, 'release', lambda: hardware.kernel))
        stack.enter_context(patch.object(u.pwd, 'getpwnam', lambda _: types.SimpleNamespace(pw_uid=1000, pw_dir=str(hardware.base/'home'))))
        stack.enter_context(patch.object(u.shutil, 'disk_usage', lambda _: types.SimpleNamespace(free=4*1024**3)))
        return stack

    def test_install_finish_and_explicit_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            with self.mocks(hardware):
                before = hardware.inventory(self.manifest)
                old_scripts = {name: hardware.path(name).read_bytes() for name in self.manifest['previous_files'] if hardware.path(name).exists()}
                u.install(BUNDLE, self.manifest, before, 'ubuntu', False)
                work = u.ACTIVE.resolve()
                state = json.loads((work/'state.json').read_text())
                self.assertEqual(state['phase'], 'awaiting-reboot')
                self.assertEqual(REAL_SHA(hardware.boot), u.BOOT_HASH)
                self.assertEqual(REAL_SHA(work/'boot-before.img'), before['boot_sha256'])
                self.assertEqual(len(list(work.glob('*.gpt'))), 6)
                self.assertEqual(hardware.flags & ~u.TRIAL_MASK, before['flags'] & ~u.TRIAL_MASK)
                self.assertEqual((hardware.flags >> 51) & 7, 7)
                self.assertEqual(hardware.path('/lib/modules/'+u.VERSION+'/modules.dep').stat().st_mode & 0o777, 0o644)
                self.assertFalse(hardware.flags & (1 << 54))
                self.assertEqual(u.normalized_gpt(hardware.tables), self.manifest['gpt_layout'])
                self.assertEqual(hardware.path('/lib/modules/'+u.VERSION).stat().st_mode & 0o777, 0o755)
                self.assertEqual(hardware.path('/etc/tb710fu/usb-ncm-host-address').stat().st_mode & 0o777, 0o600)
                self.assertNotIn(['reboot'], hardware.services)
                hardware.kernel = u.VERSION
                hardware.flags -= 1 << 51  # Normal bootloader trial decrement.
                hardware.partition['attrs'] = flag_text(hardware.flags)
                u.finish(work, state, self.manifest)
                self.assertEqual(state['phase'], 'complete')
                self.assertTrue(hardware.flags & (1 << 54))
                self.assertEqual(len(hardware.repairs), 2)
                self.assertIn(['disable', u.UNIT], hardware.services)
                u.rollback(work, state, self.manifest)
                self.assertEqual(REAL_SHA(hardware.boot), before['boot_sha256'])
                self.assertEqual(hardware.flags, before['flags'])
                self.assertFalse(u.ACTIVE.exists())
                for name, data in old_scripts.items():
                    self.assertEqual(hardware.path(name).read_bytes(), data)
                self.assertTrue(hardware.path('/lib/modules/'+TEST_OLD_VERSION+'/keep').exists())
                self.assertTrue(hardware.path('/lib/modules/'+u.VERSION).exists())

    def test_partial_write_restores_original_boot_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            with self.mocks(hardware):
                before = hardware.inventory(self.manifest)
                original = u.write_image
                def fail_new_write(source, inventory, old_hash, new_hash):
                    if new_hash == u.BOOT_HASH:
                        with hardware.boot.open('r+b') as dest:
                            dest.write(b'partial interrupted write')
                        raise u.Stop('injected write interruption')
                    return original(source, inventory, old_hash, new_hash)
                with patch.object(u, 'write_image', fail_new_write):
                    with self.assertRaisesRegex(u.Stop, 'injected write interruption'):
                        u.install(BUNDLE, self.manifest, before, 'ubuntu', False)
                self.assertEqual(REAL_SHA(hardware.boot), before['boot_sha256'])
                self.assertEqual(hardware.flags, before['flags'])
                self.assertFalse(hardware.path('/lib/modules/'+u.VERSION).exists())
                self.assertFalse(hardware.path('/opt/tb710fu-camera-test/'+u.VERSION).exists())
                self.assertFalse(u.ACTIVE.exists())

    def test_finish_network_failure_keeps_kernel_success_and_can_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            with self.mocks(hardware):
                u.install(BUNDLE, self.manifest, hardware.inventory(self.manifest), 'ubuntu', False)
                work = u.ACTIVE.resolve()
                state = json.loads((work/'state.json').read_text())
                hardware.kernel = u.VERSION
                hardware.verify_failure = True
                with self.assertRaisesRegex(u.Stop, '完整隔离测试待完成'):
                    u.finish(work, state, self.manifest)
                self.assertEqual(state['phase'], 'kernel-validated')
                self.assertTrue(hardware.flags & (1 << 54))
                self.assertEqual(REAL_SHA(hardware.boot), u.BOOT_HASH)
                hardware.verify_failure = False
                u.finish(work, state, self.manifest)
                self.assertEqual(state['phase'], 'complete')

    def test_write_refuses_changed_boot_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)/'boot'
            dest.write_bytes(b'changed by another operation')
            inventory = {'boot': str(dest), 'boot_rdev': dest.stat().st_rdev}
            with self.assertRaisesRegex(u.Stop, '写入前发生变化'):
                u.write_image(BUNDLE/'boot.img', inventory, '0'*64, u.BOOT_HASH)
            self.assertEqual(dest.read_bytes(), b'changed by another operation')

    def test_new_kernel_is_noop_even_with_reboot_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            hardware.kernel = u.VERSION
            with self.mocks(hardware):
                before = hardware.inventory(self.manifest)
                before['boot_sha256'] = u.BOOT_HASH
                with patch.object(u, 'write_image', side_effect=AssertionError('must not write')):
                    u.install(BUNDLE, self.manifest, before, 'ubuntu', True)
                self.assertEqual(hardware.services, [])
                self.assertEqual(list((Path(temp)/'private').iterdir()), [])

    def test_archive_rejects_traversal_links_and_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            for kind in ('traversal', 'symlink', 'hardlink', 'device', 'duplicate'):
                archive = Path(temp)/(kind+'.tar')
                with tarfile.open(archive,'w') as handle:
                    info = tarfile.TarInfo(u.VERSION+'/x')
                    info.size = 1
                    if kind == 'traversal': info.name = u.VERSION+'/../../outside'
                    if kind == 'symlink': info.type = tarfile.SYMTYPE; info.linkname='/etc/shadow';info.size=0
                    if kind == 'hardlink': info.type = tarfile.LNKTYPE; info.linkname='/etc/shadow';info.size=0
                    if kind == 'device': info.type = tarfile.CHRTYPE;info.size=0
                    handle.addfile(info, io.BytesIO(b'x') if info.isfile() else None)
                    if kind == 'duplicate': handle.addfile(info, io.BytesIO(b'x'))
                with self.assertRaises(u.Stop, msg=kind):u.validate_archive(archive)

    def test_gpt_guards_preserve_identity_geometry_and_unrelated_attributes(self):
        tables = {'/dev/fixture': {'partitiontable': {
            'device':'/dev/fixture','id':'disk-1','label':'gpt','sectorsize':4096,
            'partitions':[{'node':'/dev/fixture1','name':'boot_a','uuid':'part-1','start':10,'size':20,
                           'type':'fixture-type','attrs':'GUID:48,49,50,52,54'}]}}}
        changed = copy.deepcopy(tables)
        changed['/dev/fixture']['partitiontable']['partitions'][0]['attrs']='GUID:48,49,50,51,52,53'
        self.assertEqual(u.normalized_gpt(tables, identity=True),u.normalized_gpt(changed, identity=True))
        for key,value in [('uuid','different-part'),('start',11),('size',21),('attrs','GUID:48,49,55'),('attrs','GUID:48,49,50,60')]:
            changed = copy.deepcopy(tables)
            changed['/dev/fixture']['partitiontable']['partitions'][0][key]=value
            self.assertNotEqual(u.normalized_gpt(tables,identity=True),u.normalized_gpt(changed,identity=True))

    def test_screenshot_style_paired_slot_drift_and_safety_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            h = self.setup_machine(Path(temp))
            parts = {p['name']:p for w in h.tables.values()
                     for p in w['partitiontable']['partitions']}
            parts['boot_b']['attrs'] = 'GUID:49'
            parts['vbmeta_system_a']['attrs'] = 'GUID:48,49,50,51,54,60'
            self.assertEqual(u.normalized_gpt(h.tables), self.manifest['gpt_layout'])
            for flags in ('GUID:49,50', 'GUID:49,55', 'GUID:49,60'):
                parts['boot_b']['attrs'] = flags
                self.assertNotEqual(u.normalized_gpt(h.tables), self.manifest['gpt_layout'])
            parts['boot_b']['attrs'] = 'GUID:49'
            parts['vbmeta_system_a']['attrs'] = 'GUID:50,54'
            self.assertNotEqual(u.normalized_gpt(h.tables), self.manifest['gpt_layout'])

    def test_migrate_installed_camera_payload_and_restore_older_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            hardware.front_enabled = True
            with self.mocks(hardware):
                front = hardware.path('/opt/tb710fu-front-camera') / u.VERSION
                front.mkdir()
                for name in self.manifest['private_front_helpers']:
                    shutil.copyfile(Path(os.environ['TB_UPGRADE_TEST_FRONT'])/name, front/name)
                for target, name in self.manifest['camera_migration'].items():
                    if u.VERSION in target:
                        continue
                    p = hardware.path(target)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(BUNDLE/name,p)
                    p.chmod(0o700 if target.startswith('/usr/local/') else 0o644)
                hardware.path('/proc/modules').write_text(
                    'gc13a2_raw 100 0 - Live 0x0\ngc02m1_raw 100 0 - Live 0x0\n'
                    'tb_macro_registration 100 0 - Live 0x0\nleds_qcom_flash 100 0 - Live 0x0\n'
                    'v4l2_flash_led_class 100 0 - Live 0x0\ntb_flash_overlay 100 0 - Live 0x0\n')
                u.install(BUNDLE, self.manifest, hardware.inventory(self.manifest), 'ubuntu', False)
                state = json.loads((u.ACTIVE/'state.json').read_text())
                self.assertTrue(state['camera_migrated'])
                app = hardware.path('/usr/local/bin/tb-rear-camera')
                self.assertEqual(app.stat().st_mode & 0o777, 0o755)
                hardware.kernel = u.VERSION
                u.finish(u.ACTIVE.resolve(), state, self.manifest)
                u.rollback(u.ACTIVE.resolve(), state, self.manifest)
                self.assertEqual(app.stat().st_mode & 0o777, 0o700)
                self.assertFalse(hardware.path('/opt/tb710fu-flash/'+u.VERSION+'/leds-qcom-flash.ko').exists())

    def test_enabled_front_requires_matching_private_addon_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            hardware.front_enabled = True
            with self.mocks(hardware):
                before = hardware.inventory(self.manifest)
                with self.assertRaisesRegex(u.Stop, '缺少新版本'):
                    u.install(BUNDLE, self.manifest, before, 'ubuntu', False)
                self.assertEqual(REAL_SHA(hardware.boot), before['boot_sha256'])
                self.assertEqual(list((Path(temp)/'private').iterdir()), [])
                front = hardware.path('/opt/tb710fu-front-camera') / u.VERSION
                front.mkdir()
                for name in self.manifest['private_front_helpers']:
                    shutil.copyfile(Path(os.environ['TB_UPGRADE_TEST_FRONT'])/name, front/name)
                hardware.path('/proc/modules').write_text('gc13a2_raw 100 0 - Live 0x0\ntb_front_registration 100 0 - Live 0x0\nlibcomposite 100 0 - Live 0x0\n')
                u.install(BUNDLE, self.manifest, before, 'ubuntu', False)
                state = json.loads((u.ACTIVE.resolve()/'state.json').read_text())
                self.assertTrue(state['front_enabled'])

    def test_corrupt_backup_blocks_rollback_before_boot_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            with self.mocks(hardware):
                u.install(BUNDLE, self.manifest, hardware.inventory(self.manifest), 'ubuntu', False)
                work = u.ACTIVE.resolve()
                state = json.loads((work/'state.json').read_text())
                first = next(r for r in state['files_before'].values() if r['existed'])
                (work/'files-before'/first['saved']).write_bytes(b'corrupt')
                with self.assertRaisesRegex(u.Stop, '配置备份校验失败'):
                    u.rollback(work, state, self.manifest)
                self.assertEqual(REAL_SHA(hardware.boot), u.BOOT_HASH)

    def test_user_edit_blocks_rollback_before_boot_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            hardware = self.setup_machine(Path(temp))
            with self.mocks(hardware):
                u.install(BUNDLE, self.manifest, hardware.inventory(self.manifest), 'ubuntu', False)
                work = u.ACTIVE.resolve()
                state = json.loads((work/'state.json').read_text())
                hardware.path('/opt/tb710fu-camera-test/tb-camera-live.sh').write_text('user edit')
                with self.assertRaisesRegex(u.Stop, '已被其他操作修改'):
                    u.rollback(work, state, self.manifest)
                self.assertEqual(REAL_SHA(hardware.boot), u.BOOT_HASH)



if __name__ == '__main__':
    unittest.main(verbosity=2)
