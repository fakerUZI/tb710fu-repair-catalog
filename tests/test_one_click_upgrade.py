import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, call, patch


SOURCE = (Path(__file__).resolve().parents[1]
          / "contributions/one-click-upgrade/attachments/upgrade.py")
SPEC = importlib.util.spec_from_file_location("one_click_upgrade", SOURCE)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)
REVISION = "c" * 40


def item(ident, *, admin=False):
    return {"id": ident, "title": ident, "status": "community-review", "risk": "Review the fixture",
            "script": {"requires_admin": admin}}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Mock()
        self.installed = item("installed")
        self.updated = item("updated")
        self.new = item("new")
        self.missing = item("missing")
        self.catalog.refresh.return_value = {"revision": REVISION, "catalog": {"issues": [
            item(batch.BATCH_ID), item(batch.RETIRED_ID), self.installed,
            self.updated, self.new, self.missing,
        ]}}
        self.files = self.catalog.repository_files.return_value
        self.catalog.missing_items.return_value = {"missing"}
        self.catalog.cleanup_stale.return_value = (2, [])
        self.catalog.load_applied.return_value = {"issues": {
            "installed": {"fingerprint": "current-installed"},
            "updated": {"fingerprint": "older-updated"},
        }}
        self.catalog.fingerprint.side_effect = lambda candidate: "current-" + candidate["id"]
        self.catalog.applied_status.return_value = "update"
        self.catalog.downloaded.return_value = False
        self.catalog.repository_changed.return_value = False
        self.catalog.stage_execution.side_effect = lambda _item, root: root / "script.sh"

    def test_refresh_and_cleanup_happen_before_planning(self):
        revision, files, pending = batch.prepare(self.catalog, clean=True)
        self.assertEqual(pending, [(self.updated, "更新"), (self.new, "安装")])
        self.assertEqual((revision, files), (REVISION, self.files))
        self.assertEqual(self.catalog.mock_calls[:5], [
            call.refresh(), call.repository_files(REVISION),
            call.missing_items(self.catalog.refresh.return_value, self.files),
            call.cleanup_stale(self.catalog.refresh.return_value, {"missing"}), call.load_applied(),
        ])

    def test_cleanup_failure_prevents_download_and_execution(self):
        self.catalog.cleanup_stale.return_value = (0, ["cannot delete a stale file"])
        with patch.object(batch, "confirm") as confirm, self.assertRaisesRegex(RuntimeError, "清理缓存失败"):
            batch.upgrade(self.catalog, "apply")
        confirm.assert_not_called()
        self.catalog.download.assert_not_called()

    def test_check_does_not_clean_download_or_run(self):
        with patch.object(batch, "confirm") as confirm:
            self.assertEqual(batch.upgrade(self.catalog, "check"), 0)
        confirm.assert_not_called()
        self.catalog.cleanup_stale.assert_not_called()
        self.catalog.download.assert_not_called()
        self.catalog.stage_execution.assert_not_called()

    def test_download_and_stage_pending_items_and_record_only_success(self):
        self.updated["script"]["requires_admin"] = True
        with patch.object(batch, "confirm", return_value=[self.updated, self.new]), \
                patch.object(batch.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 7),
                ]) as run:
            self.assertEqual(batch.upgrade(self.catalog, "apply"), 1)
        self.assertEqual(self.catalog.download.call_args_list, [
            call(self.updated, REVISION), call(self.new, REVISION),
        ])
        first, second = [invocation.args[0] for invocation in run.call_args_list]
        self.assertEqual(first[:2], ["/usr/bin/pkexec", "/usr/bin/env"])
        self.assertEqual(second[0], "/usr/bin/env")
        for command in (first, second):
            self.assertIn("TB710FU_FIX_ACTION=apply", command)
        self.catalog.mark_applied.assert_called_once_with(self.updated, REVISION)

    def test_authentication_cancellation_stops_remaining_items(self):
        self.updated["script"]["requires_admin"] = True
        with patch.object(batch, "confirm", return_value=[self.updated, self.new]), \
                patch.object(batch.subprocess, "run", return_value=subprocess.CompletedProcess([], 126)):
            self.assertEqual(batch.upgrade(self.catalog, "apply"), 1)
        self.catalog.download.assert_called_once_with(self.updated, REVISION)
        self.catalog.mark_applied.assert_not_called()

    def test_successful_install_in_another_window_can_be_skipped(self):
        self.catalog.applied_status.return_value = "installed"
        with patch.object(batch, "confirm", return_value=[self.updated]):
            self.assertEqual(batch.upgrade(self.catalog, "apply"), 0)
        self.catalog.download.assert_not_called()
