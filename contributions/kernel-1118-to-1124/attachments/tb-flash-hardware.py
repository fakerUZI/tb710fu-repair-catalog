#!/usr/bin/python3
"""Load the reviewed TB710FU flash modules on the matching live hardware."""
import gzip
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path

VERSIONS = {'7.2.6-tb710fu-20261117', '7.2.6-tb710fu-20261118', '7.2.6-tb710fu-20261124'}
VERSION = platform.release()
ROOT = Path('/opt/tb710fu-flash') / VERSION
DT = Path('/sys/firmware/devicetree/base')
SPMI = Path('soc@0/spmi@c400000')
PMIC = SPMI / 'pmic@1'
CONTROLLER = PMIC / 'led-controller@ee00'
MODULES = Path('/sys/module')
LED = Path('/sys/class/leds/tb710fu:flash:rear')
FIRMWARE = Path('/lib/firmware/tb-flash.dtbo')
CONFIG = Path('/proc/config.gz')
FLASH_MODULES = (
    ('v4l2-flash-led-class.ko', 'v4l2_flash_led_class'),
    ('leds-qcom-flash.ko', 'leds_qcom_flash'),
    ('tb_flash_overlay.ko', 'tb_flash_overlay'),
)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def compatibles(node):
    return (node / 'compatible').read_bytes().rstrip(b'\0').split(b'\0')


def check_hardware():
    require(b'lenovo,tb710fu' in compatibles(DT), '机型不匹配')
    require(b'qcom,spmi-pmic-arb' in compatibles(DT / SPMI), 'SPMI 控制器不匹配')
    require(b'qcom,pm8550' in compatibles(DT / PMIC)
            and (DT / PMIC / 'reg').read_bytes() == struct.pack('>II', 1, 0), 'PM8550 节点不匹配')
    controller = DT / CONTROLLER
    require(b'qcom,pm8550-flash-led' in compatibles(controller)
            and (controller / 'reg').read_bytes() == struct.pack('>I', 0xEE00), '闪光灯控制器不匹配')
    if (MODULES / 'tb_flash_overlay').exists():
        check_active_led()
    else:
        require((controller / 'status').read_bytes().rstrip(b'\0') == b'disabled'
                and not any(path.is_dir() for path in controller.iterdir())
                and not (MODULES / 'leds_qcom_flash').exists() and not LED.exists(),
                '检测到已启用的其他闪光灯配置，已停止')


def check_active_led():
    require((MODULES / 'leds_qcom_flash/parameters/tb_flash_guard').read_text().strip() == '1',
            '已加载的闪光灯驱动缺少电流保护')
    node = DT / CONTROLLER / 'led-0'
    require((node / 'label').read_bytes().rstrip(b'\0') == b'tb710fu:flash:rear'
            and (node / 'led-sources').read_bytes() == struct.pack('>II', 1, 4), '闪光灯输出通道不匹配')
    for name, value in (('led-max-microamp', 200000), ('flash-max-microamp', 1000000),
                        ('flash-max-timeout-us', 500000)):
        require((node / name).read_bytes() == struct.pack('>I', value), '闪光灯限值不匹配：' + name)
    require(LED.exists(), 'flash 节点未绑定')


def preflight(installed=True):
    require(os.geteuid() == 0, '需要管理员权限')
    require(platform.machine() == 'aarch64' and VERSION in VERSIONS, '仅支持已审核的 20261117 / 20261118 ARM64 内核')
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    # The 20261118 migration retained a legacy manifest version label.
    # Check the running config and each module's actual vermagic below.
    require(hashlib.sha256(gzip.decompress(CONFIG.read_bytes())).hexdigest() == manifest['config_sha256'],
            '运行配置不匹配')
    check_hardware()
    files = manifest['files']
    for name in [*(filename for filename, _ in FLASH_MODULES), 'tb-flash.dtbo']:
        p = ROOT / name
        require(p.is_file() and not p.is_symlink() and sha(p) == files[name], '配套文件校验失败：' + name)
        if name.endswith('.ko'):
            vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', p], text=True).split()
            require(vermagic[:1] == [VERSION], '模块版本不匹配：' + name)
    if installed:
        require(sha(FIRMWARE) == files['tb-flash.dtbo'], '动态覆盖文件校验失败')


def start():
    preflight()
    if (MODULES / 'tb_flash_overlay').exists():
        (LED / 'brightness').write_text('0')
        return
    for name in ('led-class-flash', 'v4l2-async', 'videodev'):
        subprocess.run(['modprobe', name], check=True)
    loaded = []
    try:
        for filename, module in FLASH_MODULES:
            subprocess.run(['insmod', str(ROOT / filename)], check=True)
            loaded.append(module)
        check_active_led()
        (LED / 'brightness').write_text('0')
    except BaseException:
        for name in reversed(loaded):
            subprocess.run(['rmmod', name], check=False)
        raise


def stop():
    if LED.exists():
        (LED / 'brightness').write_text('0')
    for _, name in reversed(FLASH_MODULES):
        if (MODULES / name).exists():
            subprocess.run(['rmmod', name], check=True)


if __name__ == '__main__':
    try:
        {'check': preflight, 'start': start, 'stop': stop}[sys.argv[1] if len(sys.argv) > 1 else 'check']()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit('停止：' + str(exc))
