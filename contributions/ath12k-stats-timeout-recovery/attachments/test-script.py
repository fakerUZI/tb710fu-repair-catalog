#!/usr/bin/env python3
"""Isolated behavioral tests: uses a transformed copy and fake commands only."""
import os
import pathlib
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'script.sh' if HERE.name == 'attachments' else HERE / 'script.sh'

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tb-wifi-test-')
        self.base = pathlib.Path(self.tmp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        (self.base/'run/lock').mkdir(parents=True)
        (self.base/'sys/firmware/devicetree/base').mkdir(parents=True)
        (self.base/'sys/firmware/devicetree/base/model').write_bytes(b'Lenovo TB710FU\0')
        for name, val in [('ath12k/parameters/tb_poll_off','1'),('ath12k_wifi7/parameters/tb_hw_dedup','1')]:
            p = self.base/'sys/module'/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(val)
        for name in ('wlp1s0','usb0'):
            (self.base/'sys/class/net'/name).mkdir(parents=True)
        (self.base/'sys/class/net/usb0/operstate').write_text('up')
        source = SCRIPT.read_text()
        source = source.replace('/sys/', str(self.base/'sys')+'/')
        source = source.replace('/run/', str(self.base/'run')+'/')
        source = source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C TZ=UTC',
                                'export PATH="$MOCK_BIN:/usr/sbin:/usr/bin:/sbin:/bin" LC_ALL=C TZ=UTC')
        self.copy = self.base/'tested.sh'
        self.copy.write_text(source)
        self.command('uname', 'case "$1" in -m) echo aarch64;; -r) echo 7.2.6-tb710fu;; esac')
        self.command('nmcli', 'printf "wlp1s0:wifi:connected\\n"')
        self.command('ip', 'echo "2: usb0 inet 172.16.42.1/24 scope global usb0"')
        self.command('iw', 'printf "signal: -45 dBm\\n"')
        self.command('journalctl', 'if [ "${MOCK_LOGS:-0}" = 1 ] && [ ! -f "$MOCK_ROOT/run/tb710fu-wifi-stats-recovery/last" ]; then printf "time out while waiting for fw stats done\\ntime out while waiting for fw stats done\\n"; fi')
        self.command('rmmod', '''echo "rmmod $1" >> "$MOCK_ROOT/calls"
if [ "$1" = ath12k ] && [ "${FAIL_CORE:-0}" = 1 ]; then exit 1; fi
mv "$MOCK_ROOT/sys/module/$1" "$MOCK_ROOT/$1.saved"''')
        self.command('modprobe', '''echo "modprobe $1" >> "$MOCK_ROOT/calls"
for m in ath12k ath12k_wifi7; do
  if [ -d "$MOCK_ROOT/$m.saved" ]; then mv "$MOCK_ROOT/$m.saved" "$MOCK_ROOT/sys/module/$m"; fi
done''')
        self.command('sleep', ':')
        self.env = dict(os.environ, MOCK_ROOT=str(self.base), MOCK_BIN=str(self.bin), TB710FU_FIX_ACTION="apply")
        self.env.pop('SSH_CONNECTION', None)

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, name, content):
        p = self.bin/name
        p.write_text('#!/bin/sh\nset -eu\n'+content+'\n')
        p.chmod(0o755)

    def run_script(self, **extra):
        return subprocess.run(['bash',str(self.copy)], env=dict(self.env, **extra),
                              text=True, capture_output=True, timeout=10)

    def calls(self):
        p=self.base/'calls'
        return p.read_text() if p.exists() else ''

    def test_healthy_repeated_is_noop(self):
        for _ in range(2): self.assertEqual(self.run_script().returncode,0)
        self.assertEqual(self.calls(),'')

    def test_check_never_reloads(self):
        self.assertEqual(self.run_script(MOCK_LOGS='1', TB710FU_FIX_ACTION='check').returncode,0)
        self.assertEqual(self.calls(),'')

    def test_common_check_is_readonly_even_with_errors(self):
        r=self.run_script(MOCK_LOGS='1',TB710FU_FIX_ACTION='check')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.calls(),'')
        self.assertFalse((self.base/'run/tb710fu-wifi-stats-recovery').exists())

    def test_standalone_default_is_readonly(self):
        self.env.pop('TB710FU_FIX_ACTION')
        r=self.run_script(MOCK_LOGS='1')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.calls(),'')
        self.assertFalse((self.base/'run/tb710fu-wifi-stats-recovery').exists())

    def test_unsupported_rollback_has_no_writes(self):
        r=self.run_script(MOCK_LOGS='1',TB710FU_FIX_ACTION='rollback')
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.calls(),'')
        self.assertFalse((self.base/'run/tb710fu-wifi-stats-recovery').exists())

    def test_repair_then_repeat_reloads_once(self):
        for _ in range(2): self.assertEqual(self.run_script(MOCK_LOGS='1').returncode,0)
        self.assertEqual(self.calls().count('rmmod ath12k_wifi7'),1)
        self.assertEqual(self.calls().count('rmmod ath12k\n'),1)
        self.assertEqual(self.calls().count('modprobe ath12k_wifi7'),1)

    def test_failed_core_unload_restores_device_module(self):
        r=self.run_script(MOCK_LOGS='1', FAIL_CORE='1')
        self.assertNotEqual(r.returncode,0)
        self.assertTrue((self.base/'sys/module/ath12k_wifi7').is_dir())
        self.assertIn('modprobe ath12k_wifi7', self.calls())
        self.assertFalse((self.base/'run/tb710fu-wifi-stats-recovery/last').exists())

    def test_missing_usb_refuses_local_too(self):
        (self.base/'sys/class/net/usb0/operstate').write_text('down')
        r=self.run_script(MOCK_LOGS='1')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.calls(),'')

    def test_ssh_not_over_usb_refuses(self):
        r=self.run_script(MOCK_LOGS='1', SSH_CONNECTION='192.0.2.1 1234 192.0.2.2 22')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.calls(),'')

    def test_ssh_over_usb_can_repair(self):
        r=self.run_script(MOCK_LOGS='1', SSH_CONNECTION='172.16.42.6 1234 172.16.42.1 22')
        self.assertEqual(r.returncode,0, r.stdout+r.stderr)
        self.assertIn('rmmod ath12k_wifi7',self.calls())

    def test_different_model_refuses(self):
        (self.base/'sys/firmware/devicetree/base/model').write_text('Other')
        r=self.run_script(MOCK_LOGS='1')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.calls(),'')

    def test_wrong_parameter_refuses(self):
        (self.base/'sys/module/ath12k/parameters/tb_poll_off').write_text('0')
        r=self.run_script(MOCK_LOGS='1')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.calls(),'')

if __name__ == '__main__': unittest.main(verbosity=2)
