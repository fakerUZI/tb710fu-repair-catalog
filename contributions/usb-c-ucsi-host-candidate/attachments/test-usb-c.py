#!/usr/bin/env python3
"""State-machine tests use synthetic sysfs only; never touch the tablet."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('usb_c', Path(__file__).with_name('usb-c-test.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 0.0
        self.request_count = 0
        self.p = m.Probe(self.root, self.root / 'config', clock=lambda: self.now,
                         sleep=self.sleep, run=lambda *a, **kw: subprocess.CompletedProcess(a, 0, 'inactive\n'))
        self.put(m.ROLE, 'device')
        self.put(m.PORT + '/data_role', 'host [device]')
        self.put('class/net/usb0/carrier', '0')
        self.bind()
        self.state = {'role': 'device', 'ucsi_bound': True, 'partner': False,
                      'data_role': 'device', 'usb': []}
        self.p.snapshot = lambda: dict(self.state)

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def bind(self):
        path = self.root / m.DRIVER / m.DEVICE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(self.root, target_is_directory=True)

    def sleep(self, n):
        self.now += n

    def test_no_partner_no_role_write(self):
        before = (self.root / m.PORT / 'data_role').read_text()
        self.assertEqual(self.p.host(5, lambda _: None), 3)
        self.assertEqual((self.root / m.PORT / 'data_role').read_text(), before)
        self.assertEqual((self.root / m.ROLE).read_text(), 'device')

    def test_single_negotiated_request_does_not_claim_success(self):
        self.state['partner'] = True
        original = Path.write_text
        writes = []
        def record(path, text, *a, **kw):
            writes.append((path, text))
            return original(path, text, *a, **kw)
        with patch.object(Path, 'write_text', record):
            self.assertEqual(self.p.host(5, lambda _: None), 3)
        self.assertEqual(writes, [(self.root / m.PORT / 'data_role', 'host\n')])
        self.assertEqual((self.root / m.ROLE).read_text(), 'device')

    def test_host_without_enumeration_is_failure(self):
        self.state.update(partner=True, role='host', data_role='host')
        self.assertEqual(self.p.host(5, lambda _: None), 3)

    def test_observes_stable_new_enumeration(self):
        def sleep(n):
            self.now += n
            self.state.update(partner=True, role='host', data_role='host', usb=[{'vid_pid': '152d:0562'}])
        self.p.sleep = sleep
        self.assertEqual(self.p.host(5, lambda _: None), 0)
        self.assertGreaterEqual(self.now, 3.1)

    def test_transient_enumeration_is_failure(self):
        def sleep(n):
            self.now += n
            self.state.update(partner=True, role='host', data_role='host',
                              usb=[{'vid_pid': '152d:0562'}] if 1 < self.now < 2 else [])
        self.p.sleep = sleep
        self.assertEqual(self.p.host(5, lambda _: None), 3)

    def test_preexisting_device_rejected(self):
        self.state['usb'] = [{'vid_pid': '152d:0562'}]
        with self.assertRaisesRegex(RuntimeError, '已有'):
            self.p.host(5)

    def test_active_rescue_service_rejected(self):
        self.p.run = lambda *a, **kw: subprocess.CompletedProcess(a, 0, 'active\n')
        with self.assertRaisesRegex(RuntimeError, '服务'):
            self.p.host(5)

    def test_service_query_error_rejected(self):
        self.p.run = lambda *a, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(1, a))
        with self.assertRaises(subprocess.CalledProcessError):
            self.p.host(5)

    def test_usb_network_rejected_even_without_ssh_environment(self):
        self.put('class/net/usb0/carrier', '1')
        with self.assertRaisesRegex(RuntimeError, '救援链路'):
            self.p.host(5)

    def test_bound_gadget_rejected(self):
        self.put('config/usb_gadget/g1/UDC', 'a600000.usb')
        with self.assertRaisesRegex(RuntimeError, 'UDC'):
            self.p.host(5)

    def test_detached_ucsi_rejected(self):
        (self.root / m.DRIVER / m.DEVICE).unlink()
        with self.assertRaisesRegex(RuntimeError, '解绑'):
            self.p.host(5)

    def test_rejected_swap_not_retried(self):
        self.state['partner'] = True
        with patch.object(Path, 'write_text', side_effect=OSError('not supported')) as write:
            with self.assertRaises(OSError):
                self.p.host(5)
            self.assertEqual(write.call_count, 1)

    def test_ucsi_disappears_abort(self):
        self.state['ucsi_bound'] = False
        with self.assertRaisesRegex(RuntimeError, 'UCSI 被解绑'):
            self.p.host(5)

    def test_snapshot_does_not_export_private_usb_fields(self):
        self.put('bus/usb/devices/1-1/idVendor', '152d')
        self.put('bus/usb/devices/1-1/idProduct', '0562')
        self.put('bus/usb/devices/1-1/serial', 'PRIVATE-SERIAL')
        self.put('bus/usb/devices/1-1/product', 'PRIVATE-PRODUCT')
        self.put('bus/usb/devices/usb1/idVendor', '1d6b')
        self.put('bus/usb/devices/usb1/idProduct', '0002')
        result = m.Probe(self.root).snapshot()
        self.assertEqual(result['usb'], [{'vid_pid': '152d:0562', 'speed': None}])
        self.assertNotIn('PRIVATE', str(result))

    def test_trace_report_omits_task_identity_and_unrelated_events(self):
        import contextlib
        import io
        trace = m.Trace(self.root)
        trace.instance.mkdir(parents=True)
        (trace.instance / 'tracing_on').write_text('1')
        (trace.instance / 'trace').write_text(
            'PRIVATE-PROCESS-123 [001] ... 12.345: ucsi_connector_change: port0 status: connected=1\n'
            'PRIVATE-PROCESS-123 [001] ... 12.346: sched_switch: PRIVATE-DATA\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            trace.report()
        self.assertIn('connected=1', out.getvalue())
        self.assertNotIn('PRIVATE', out.getvalue())

    def test_watch_performs_no_writes(self):
        self.p = m.Probe(self.root, clock=lambda: self.now, sleep=self.sleep)
        with patch.object(Path, 'write_text', side_effect=AssertionError('unexpected write')):
            self.assertEqual(self.p.watch(5, lambda _: None), 0)

    def test_trace_uses_private_instance_and_cleans_on_interrupt(self):
        (self.root / 'instances').mkdir()
        global_switch = self.put('tracing_on', 'GLOBAL-UNCHANGED')
        trace = m.Trace(self.root)
        writes = []
        def record(path, text):
            writes.append(path)
            return len(text)
        with patch.object(Path, 'write_text', record):
            with self.assertRaises(KeyboardInterrupt):
                with trace:
                    raise KeyboardInterrupt()
        self.assertFalse(trace.instance.exists())
        self.assertTrue(all(trace.instance in p.parents for p in writes))
        self.assertEqual(global_switch.read_text(), 'GLOBAL-UNCHANGED')

    def test_trace_partial_setup_is_cleaned(self):
        (self.root / 'instances').mkdir()
        trace = m.Trace(self.root)
        count = 0
        def fail_setup(path, text):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('buffer unavailable')
            return len(text)
        with patch.object(Path, 'write_text', fail_setup):
            with self.assertRaises(OSError):
                with trace:
                    self.fail('setup should fail')
        self.assertFalse(trace.instance.exists())


if __name__ == '__main__':
    unittest.main()
