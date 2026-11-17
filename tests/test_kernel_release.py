from datetime import date
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('kernel_release', ROOT / 'contributions/snap-kernel-offline-upgrade/attachments/kernel-release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class Release(unittest.TestCase):
    def setUp(self):
        self.plan = {'format': 1, 'source_version': '7.2.6-tb710fu-snap1', 'target_version': '7.2.6-tb710fu-20261117'}
        self.today = date(2026, 11, 17)

    def check(self, plan=None, current=None):
        return release.validate(plan or self.plan, current or self.plan['source_version'], self.today)

    def test_first_dated_release_accepts_exact_snap1_source(self):
        self.assertEqual(self.check(), '7.2.6-tb710fu-20261117')

    def test_different_old_kernel_or_already_target_is_rejected(self):
        for current in ['7.2.6-tb710fu', '7.2.6-tb710fu-20261115', self.plan['target_version'], '7.2.7-tb710fu-20261116']:
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.check(current=current)

    def test_next_dated_release(self):
        self.plan.update(source_version='7.2.6-tb710fu-20261117', target_version='7.2.6-tb710fu-20261118')
        self.assertEqual(release.validate(self.plan, self.plan['source_version'], date(2026, 11, 18)), self.plan['target_version'])

    def test_same_or_older_date_rejected_even_for_newer_linux(self):
        self.plan['source_version'] = '7.2.6-tb710fu-20261117'
        for target in ['7.2.6-tb710fu-20261117', '7.2.6-tb710fu-20261116', '7.3.0-tb710fu-20261116']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.check(dict(self.plan, target_version=target))

    def test_future_date_is_not_fabricated(self):
        with self.assertRaises(ValueError):
            self.check(dict(self.plan, target_version='7.2.6-tb710fu-20261118'))

    def test_invalid_dates_and_suffixes(self):
        for target in ['7.2.6-tb710fu-20260230', '7.2.6-tb710fu-20261117.1', '7.2.6-tb710fu-2026117', '7.2.6-tb710fu-snap2', '7.2.6-tb710fu-20261117\n']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.check(dict(self.plan, target_version=target))

    def test_unknown_source_is_not_assigned_an_invented_date(self):
        with self.assertRaises(ValueError):
            self.check(dict(self.plan, source_version='7.2.6-tb710fu-custom'))

    def test_linux_downgrade_rejected(self):
        with self.assertRaises(ValueError):
            self.check(dict(self.plan, source_version='7.3.0-tb710fu-20261116'))

    def test_bad_plan(self):
        for changes in [{'format': 2}, {'target_version': None}, {'override': True}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(dict(self.plan, **changes))


class UpgradeGate(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('assembler', ROOT / 'contributions/snap-kernel-offline-upgrade/attachments/assemble-kernel-bundle.py')
        self.assembler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.assembler)
        self.installer = SimpleNamespace(OLD_VERSION='7.2.6-tb710fu-snap1', VERSION='7.2.6-tb710fu-20261117')
        self.spec = {'format': 2, 'source_version': self.installer.OLD_VERSION, 'target_version': self.installer.VERSION}

    def test_actual_upgrade_gate_accepts_source(self):
        self.assembler.check_release(self.spec, self.installer, 'install', self.installer.OLD_VERSION)

    def test_gate_rejects_other_current_versions_before_installer(self):
        for current in ['7.2.6-tb710fu', self.installer.VERSION, '7.2.6-tb710fu-20261118']:
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.assembler.check_release(self.spec, self.installer, 'install', current)

    def test_gate_cannot_lie_about_installer_versions(self):
        with self.assertRaises(ValueError):
            self.assembler.check_release(dict(self.spec, target_version='7.2.6-tb710fu-20261116'), self.installer, 'validate')

    def test_format_one_cannot_bypass_date_policy(self):
        with self.assertRaises(ValueError):
            self.assembler.check_release(dict(self.spec, format=1), self.installer, 'install', self.installer.OLD_VERSION)

    def test_readonly_archive_validation_does_not_require_device(self):
        self.assembler.check_release(self.spec, self.installer, 'validate', 'Darwin')

    def test_rollback_can_run_from_target(self):
        self.assembler.check_release(self.spec, self.installer, 'rollback', self.installer.VERSION)

    def test_legacy_installer_preserves_already_target_noop(self):
        from types import SimpleNamespace
        legacy = SimpleNamespace(OLD_VERSION='7.2.6-tb710fu', VERSION='7.2.6-tb710fu-snap1')
        spec = {'format': 1, 'source_version': legacy.OLD_VERSION, 'target_version': legacy.VERSION}
        self.assembler.check_release(spec, legacy, 'install', legacy.VERSION)
        with self.assertRaises(ValueError):
            self.assembler.check_release(spec, legacy, 'install', '7.2.6-tb710fu-20261117')


if __name__ == '__main__':
    unittest.main()
