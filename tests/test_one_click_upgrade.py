import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch


SOURCE = (Path(__file__).resolve().parents[1]
          / "contributions/one-click-upgrade/attachments/upgrade.py")
LAUNCHER = Path("/usr/share/tb710fu-repair-assistant/one-click-upgrade.py")
WORKER = Path("/usr/libexec/tb710fu-repair-assistant-run-batch")


class BatchLauncherTests(unittest.TestCase):
    def setUp(self):
        self.execute = self.enterContext(patch.object(os, "execv"))

    def invoke(self, available, args):
        self.execute.reset_mock()
        with patch.object(Path, "is_file", lambda path: path in available), \
                patch.object(sys, "argv", [str(SOURCE), *args]):
            runpy.run_path(str(SOURCE), run_name="__main__")
        return self.execute

    def test_requires_the_assistant_and_batch_worker(self):
        for available in (set(), {LAUNCHER}, {WORKER}):
            with self.subTest(available=available):
                with self.assertRaisesRegex(SystemExit, "更新修复助手"):
                    self.invoke(available, ["apply"])
                self.execute.assert_not_called()

    def test_forwards_the_action(self):
        for action in ("apply", "check"):
            with self.subTest(action=action):
                execute = self.invoke({LAUNCHER, WORKER}, [action])
                execute.assert_called_once_with(
                    "/usr/bin/python3", ["/usr/bin/python3", "-IBu", str(LAUNCHER), action],
                )

    def test_forwards_a_pinned_selection(self):
        selection = '{"touch-window-grip": "' + "a" * 64 + '"}'
        args = ["apply", "--selection", selection]
        execute = self.invoke({LAUNCHER, WORKER}, args)
        execute.assert_called_once_with(
            "/usr/bin/python3", ["/usr/bin/python3", "-IBu", str(LAUNCHER), *args],
        )
