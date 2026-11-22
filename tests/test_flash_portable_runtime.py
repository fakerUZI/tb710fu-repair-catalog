import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parents[1] /
          'contributions/flash-portable-runtime/attachments/tb-flash-hardware.py')
spec = importlib.util.spec_from_file_location('portable_flash', SCRIPT)
flash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flash)


class PortableFlashTests(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.dt = base / 'dt'
        self.modules = base / 'modules'
        self.led = base / 'led'
        self.root = base / 'payload'
        self.config = base / 'config.gz'
        self.overlay = base / 'tb-flash.dtbo'
        self.controller = self.dt / flash.CONTROLLER
        self.controller.mkdir(parents=True)
        self.root.mkdir()
        self.version = '7.2.6-tb710fu-20261118'
        self.files = {}
        for name in ['tb-flash.dtbo', *(name for name, _ in flash.FLASH_MODULES)]:
            path = self.root / name
            path.write_bytes(name.encode())
            self.files[name] = flash.sha(path)
        self.overlay.write_bytes((self.root / 'tb-flash.dtbo').read_bytes())
        self.config.write_bytes(gzip.compress(b'reviewed config'))
        self.manifest = {'version': '7.2.6-tb710fu-20261117', 'files': self.files,
                         'config_sha256': hashlib.sha256(b'reviewed config').hexdigest()}
        self.write_manifest()
        for path, data in {
            'compatible': b'lenovo,tb710fu\0',
            flash.SPMI / 'compatible': b'qcom,spmi-pmic-arb\0',
            flash.PMIC / 'compatible': b'qcom,pm8550\0',
            flash.PMIC / 'reg': struct.pack('>II', 1, 0),
            flash.CONTROLLER / 'compatible': b'qcom,pm8550-flash-led\0',
            flash.CONTROLLER / 'reg': struct.pack('>I', 0xEE00),
            flash.CONTROLLER / 'status': b'disabled\0',
        }.items():
            (self.dt / path).write_bytes(data)
        self.enterContext(patch.multiple(flash, DT=self.dt, ROOT=self.root, CONFIG=self.config,
                                         MODULES=self.modules, LED=self.led, FIRMWARE=self.overlay,
                                         VERSION=self.version))
        self.enterContext(patch.object(flash.os, 'geteuid', return_value=0))
        self.enterContext(patch.object(flash.platform, 'machine', return_value='aarch64'))
        self.modinfo = self.enterContext(patch.object(flash.subprocess, 'check_output',
                                                     return_value=self.version + ' SMP'))

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_old_disk_and_boot_pins_are_not_consulted(self):
        self.manifest.update(boot_sha256='different', gpt_layout=[{'size': 21}], firmware={'abl_a': 'different'})
        self.write_manifest()
        flash.preflight()
        commands = [entry.args[0][:3] for entry in self.modinfo.call_args_list]
        self.assertEqual(commands, [['modinfo', '-F', 'vermagic']] * 3)

    def test_rejects_unreviewed_hardware_or_abi(self):
        (self.controller / 'reg').write_bytes(struct.pack('>I', 0xDC00))
        with self.assertRaisesRegex(RuntimeError, '控制器不匹配'):
            flash.preflight()
        (self.controller / 'reg').write_bytes(struct.pack('>I', 0xEE00))
        self.modinfo.return_value = 'different-kernel SMP'
        with self.assertRaisesRegex(RuntimeError, '模块版本不匹配'):
            flash.preflight()

    def test_rejects_overlay_change_or_foreign_flash(self):
        self.overlay.write_bytes(b'wrong overlay')
        with self.assertRaisesRegex(RuntimeError, '动态覆盖文件校验失败'):
            flash.preflight()
        (self.controller / 'led-1').mkdir()
        with self.assertRaisesRegex(RuntimeError, '其他闪光灯配置'):
            flash.preflight()

    def test_active_driver_checks_channels_and_limits(self):
        (self.modules / 'tb_flash_overlay').mkdir(parents=True)
        guard = self.modules / 'leds_qcom_flash/parameters/tb_flash_guard'
        guard.parent.mkdir(parents=True)
        guard.write_text('1\n')
        self.led.mkdir()
        node = self.controller / 'led-0'
        node.mkdir()
        (node / 'label').write_bytes(b'tb710fu:flash:rear\0')
        (node / 'led-sources').write_bytes(struct.pack('>II', 1, 4))
        for name, number in [('led-max-microamp', 200000), ('flash-max-microamp', 1000000),
                             ('flash-max-timeout-us', 500000)]:
            (node / name).write_bytes(struct.pack('>I', number))
        flash.preflight()
        (node / 'flash-max-microamp').write_bytes(struct.pack('>I', 1500000))
        with self.assertRaisesRegex(RuntimeError, '限值不匹配'):
            flash.preflight()


if __name__ == '__main__':
    unittest.main()
