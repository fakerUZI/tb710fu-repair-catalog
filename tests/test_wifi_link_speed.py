import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


INSTALLER = Path(__file__).resolve().parents[1] / 'contributions/wifi-link-speed/attachments/install.py'
SPEC = importlib.util.spec_from_file_location('wifi_link_speed', INSTALLER)
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)
ORIGINAL = (b'import os\nimport socket\n\n' + REPAIR.HANDLER + b'    if network:\n' +
            REPAIR.SETTER + b'\n        pass\n')


class WifiRepairTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.program = Path(folder) / 'tb-wifi-compat.py'
        self.program.write_bytes(ORIGINAL)
        self.program.chmod(0o755)
        self.enterContext(patch.object(REPAIR, 'PROGRAM', self.program))
        self.enterContext(patch.object(REPAIR, 'PROGRAM_HASH', REPAIR.digest(ORIGINAL)))
        self.enterContext(patch.object(REPAIR, 'owned_file', return_value=0o755))
        self.selection = self.enterContext(patch.object(REPAIR, 'current_selection', return_value=(b'"Lab AP"', b'1')))
        self.restart = self.enterContext(patch.object(REPAIR, 'restart'))

    def test_check_leaves_baseline_in_place(self):
        REPAIR.operate('check')
        self.assertEqual(self.program.read_bytes(), ORIGINAL)
        self.restart.assert_not_called()

    def test_apply_repeat_and_rollback(self):
        REPAIR.operate('apply')
        applied = self.program.read_bytes()
        self.assertEqual(REPAIR.original_and_selection(applied), (ORIGINAL, b'"Lab AP"'))
        self.selection.return_value = (b'"Lab AP"', b'0')
        REPAIR.operate('apply')
        self.assertEqual(self.program.read_bytes(), applied)
        REPAIR.operate('rollback')
        self.assertEqual(self.program.read_bytes(), ORIGINAL)
        self.assertEqual(self.program.stat().st_mode & 0o777, 0o755)

    def test_leaves_unrecognized_manual_edits_alone(self):
        edited = REPAIR.patched(ORIGINAL, b'"Lab AP"').replace(b'timeout(1)', b'timeout(2)')
        self.program.write_bytes(edited)
        with self.assertRaises(ValueError):
            REPAIR.operate('rollback')
        self.assertEqual(self.program.read_bytes(), edited)
        self.restart.assert_not_called()

    def test_declines_apply_on_unrestricted_connection(self):
        self.selection.return_value = (b'"Lab AP"', b'0')
        with self.assertRaises(ValueError):
            REPAIR.operate('apply')
        self.assertEqual(self.program.read_bytes(), ORIGINAL)

    def test_keeps_previous_network_as_the_only_exception(self):
        REPAIR.operate('apply')
        installed = self.program.read_bytes()
        self.selection.return_value = (b'"Guest AP"', b'1')
        with self.assertRaises(ValueError):
            REPAIR.operate('apply')
        self.assertEqual(self.program.read_bytes(), installed)

    def test_failed_restart_restores_previous_file(self):
        self.restart.side_effect = [RuntimeError('failure'), None]
        with self.assertRaises(RuntimeError):
            REPAIR.operate('apply')
        self.assertEqual(self.program.read_bytes(), ORIGINAL)
        self.assertEqual(self.restart.call_count, 2)

    def test_persists_exact_ssid_as_data(self):
        for ssid in (b'"Mixed \'quotes\' and \\ path"', b'e4bea7'):
            with self.subTest(ssid=ssid):
                installed = REPAIR.patched(ORIGINAL, ssid)
                self.assertEqual(REPAIR.original_and_selection(installed), (ORIGINAL, ssid))
        with self.assertRaises(ValueError):
            REPAIR.patched(ORIGINAL, b'FAIL')

    def test_runtime_queries_only_ssid_and_matches_whole_name(self):
        scope = {}
        exec(compile(REPAIR.patched(ORIGINAL, b'"Lab AP"'), '<compatibility daemon>', 'exec'), scope)
        with patch.object(scope['socket'], 'socket') as create:
            control = MagicMock()
            create.return_value.__enter__.return_value = control
            control.recv.side_effect = [b'"Lab AP"\n', b'"Lab AP Guest"\n', b'FAIL\n']
            lookup = scope['use_normal_negotiation']
            self.assertTrue(lookup('/Interfaces/0/Networks/7'))
            self.assertFalse(lookup('/Interfaces/0/Networks/8'))
            self.assertFalse(lookup('/Interfaces/0/Networks/9'))
            self.assertEqual([call.args[0] for call in control.send.call_args_list],
                             [b'GET_NETWORK 7 ssid', b'GET_NETWORK 8 ssid', b'GET_NETWORK 9 ssid'])
