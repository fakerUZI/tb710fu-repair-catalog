from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'contributions/kernel-offline-upgrade/attachments'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


release = module('kernel_release', BASE / 'kernel-release.py')


class Release(unittest.TestCase):
    def setUp(self):
        self.plan = {'format': 2, 'source_policy': 'undated', 'target_version': '7.2.6-tb710fu-20261117'}
        self.today = date(2026, 11, 17)

    def check(self, current, plan=None):
        return release.validate(plan or self.plan, current, self.today)

    def test_all_undated_versions_without_an_allowlist(self):
        for current in ['7.2.6-tb710fu', '7.2.6-tb710fu-snap1', '7.2.6-tb710fu-snap2', '7.2.6-tb710fu-custom', '6.17.0-1004-qcom', '7.3.0-custom', '7.2.6', '7.2.6-custom+abc123']:
            with self.subTest(current=current):
                self.assertEqual(self.check(current), self.plan['target_version'])

    def test_initial_migration_rejects_all_dated_versions(self):
        for current in ['7.2.6-tb710fu-20261116', self.plan['target_version'], '7.2.6-tb710fu-20261118', '7.2.6-custom-2026-11-16', '7.2.6-custom-2026.11.16']:
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.check(current)

    def test_invalid_date_token_cannot_masquerade_as_undated(self):
        for current in ['7.2.6-tb710fu-20260230', '7.2.6-custom-20261399', '7.2.6-custom-20261117-extra']:
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.check(current)

    def test_no_legacy_format_or_exact_source_bypass(self):
        for plan in [dict(self.plan, format=1), dict(self.plan, source_version='7.2.6-tb710fu-snap1'), dict(self.plan, source_policy='all')]:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.check('7.2.6-tb710fu-snap1', plan)

    def test_future_updates_accept_any_earlier_dated_version(self):
        plan = dict(self.plan, source_policy='dated-before-target')
        for current in ['7.2.6-tb710fu-20261115', '7.2.6-tb710fu-20261116']:
            self.assertEqual(self.check(current, plan), plan['target_version'])

    def test_future_updates_reject_undated_versions(self):
        with self.assertRaises(ValueError):
            self.check('7.2.6-tb710fu-snap1', dict(self.plan, source_policy='dated-before-target'))

    def test_same_or_newer_date_is_rejected(self):
        plan = dict(self.plan, source_policy='dated-before-target')
        for current in [self.plan['target_version'], '7.2.6-tb710fu-20261118']:
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.check(current, plan)

    def test_next_day_release(self):
        plan = dict(self.plan, source_policy='dated-before-target', target_version='7.2.6-tb710fu-20261118')
        self.assertEqual(release.validate(plan, self.plan['target_version'], date(2026, 11, 18)), plan['target_version'])

    def test_future_release_cannot_accidentally_keep_undated_policy(self):
        plan = dict(self.plan, target_version='7.2.6-tb710fu-20261118')
        with self.assertRaisesRegex(ValueError, '后续发布必须'):
            release.validate(plan, '7.2.6-tb710fu-snap1', date(2026, 11, 18))

    def test_future_target_not_fabricated(self):
        with self.assertRaisesRegex(ValueError, '目标日期晚于'):
            self.check('7.2.6-tb710fu-20261116', dict(self.plan, source_policy='dated-before-target', target_version='7.2.6-tb710fu-20261118'))

    def test_target_dates_and_suffixes(self):
        for target in ['7.2.6-tb710fu-20260230', '7.2.6-tb710fu-20261117.1', '7.2.6-tb710fu-2026117', '7.2.6-tb710fu-snap2', '7.2.6-tb710fu-20261117\n']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.check('7.2.6-tb710fu', dict(self.plan, target_version=target))

    def test_malformed_current(self):
        for current in ['', 'Darwin', '7.2.6\n', '7.2.6;reboot', '7.2.6-' + 'a' * 129]:
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.check(current)


class UpgradeGate(unittest.TestCase):
    def setUp(self):
        self.assembler = module('assembler', BASE / 'assemble-kernel-bundle.py')
        self.plan = json.loads((BASE / 'kernel-release.json').read_text())
        self.installer = SimpleNamespace(RELEASE_PLAN=self.plan, VERSION=self.plan['target_version'])
        self.spec = {'format': 2, 'release': self.plan}

    def test_actual_gate_accepts_undated_without_snap1_allowlist(self):
        for current in ['7.2.6-tb710fu', '7.2.6-tb710fu-snap1', '7.2.6-tb710fu-snap99']:
            self.assembler.check_release(self.spec, self.installer, 'install', current)

    def test_actual_gate_rejects_equal_or_newer_sources(self):
        with self.assertRaises(ValueError):
            self.assembler.check_release(self.spec, self.installer, 'install', self.plan['target_version'])

    def test_actual_gate_accepts_17_and_an_earlier_valid_date(self):
        for current in ('7.2.6-tb710fu-20261117', '7.2.6-tb710fu-20261116'):
            self.assembler.check_release(self.spec, self.installer, 'install', current)
        for current in ('7.2.6-tb710fu-20261119', '7.2.6-custom-2026-11-17', '7.2.6-tb710fu-20260230'):
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.assembler.check_release(self.spec, self.installer, 'install', current)

    def test_bridge_policy_cannot_leak_into_newer_release(self):
        with self.assertRaises(ValueError):
            release.validate(dict(self.plan, target_version='7.2.6-tb710fu-20261119'),
                             '7.2.6-tb710fu-snap1', date(2026, 11, 19))

    def test_metadata_cannot_lie_about_installer_policy(self):
        bad = dict(self.spec, release=dict(self.plan, source_policy='dated-before-target'))
        with self.assertRaises(ValueError):
            self.assembler.check_release(bad, self.installer, 'validate')

    def test_legacy_payload_format_removed(self):
        with self.assertRaises(ValueError):
            self.assembler.check_release(dict(self.spec, format=1), self.installer, 'install', '7.2.6-tb710fu')

    def test_rollback_is_not_rejected_as_a_forward_upgrade(self):
        self.assembler.check_release(self.spec, self.installer, 'rollback', self.plan['target_version'])

    def unbuilt(self, root):
        path = Path(root)
        (path/'kernel-release.json').write_text(json.dumps(self.plan))
        (path/'bundle-parts.json').write_text(json.dumps({'format': 2, 'release': self.plan, 'payload_status': 'not-built', 'parts': []}))
        return path

    def test_missing_payload_check_explicitly_reports_not_installable(self):
        with tempfile.TemporaryDirectory() as root:
            argv = ['assemble-kernel-bundle.py', '--attachments-dir', str(self.unbuilt(root)), '--action', 'check']
            with patch.object(sys, 'argv', argv), patch.object(self.assembler, 'environment'), patch.object(self.assembler.platform, 'release', return_value='7.2.6-tb710fu-snap2'):
                self.assertEqual(self.assembler.main(), 0)

    def test_install_without_payload_stops_before_staging(self):
        with tempfile.TemporaryDirectory() as root:
            argv = ['assemble-kernel-bundle.py', '--attachments-dir', str(self.unbuilt(root)), '--action', 'install']
            with patch.object(sys, 'argv', argv), patch.object(self.assembler, 'environment'), patch.object(self.assembler.os, 'geteuid', return_value=0), patch.object(self.assembler.platform, 'release', return_value='7.2.6-tb710fu-snap1'), patch.object(self.assembler.tempfile, 'TemporaryDirectory') as stage:
                with self.assertRaisesRegex(ValueError, '尚未构建'):
                    self.assembler.main()
                stage.assert_not_called()

    def test_readonly_check_can_report_already_target(self):
        self.assembler.check_release(self.spec, self.installer, 'check', self.plan['target_version'])

    def test_sole_kernel_entry_is_first_and_old_payloads_are_gone(self):
        catalog = json.loads((ROOT / 'catalog.json').read_text())
        entries = [x for x in catalog['issues'] if 'kernel' in x['id'] and 'upgrade' in x['id']]
        self.assertEqual(len(entries), 1)
        self.assertEqual(catalog['issues'][0]['id'], 'kernel-offline-upgrade')
        self.assertFalse((ROOT / 'contributions/snap-kernel-offline-upgrade').exists())
        spec = json.loads((BASE/'bundle-parts.json').read_text())
        self.assertEqual(spec['format'], 2)
        self.assertEqual(spec['payload_status'], 'ready')
        self.assertEqual(spec['release'], self.plan)
        self.assertTrue(all(p.name.startswith('kernel-upgrade.part') for p in BASE.glob('kernel-upgrade.part*')))
        self.assertEqual(len(list(BASE.glob('kernel-upgrade.part*'))), len(spec['parts']))


if __name__ == '__main__':
    unittest.main()
