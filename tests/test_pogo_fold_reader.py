import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
import unittest


ASSET = Path(__file__).resolve().parents[1] / "contributions/pogo-osk/attachments/fold-reader.py"
SPEC = importlib.util.spec_from_file_location("pogo_fold_reader", ASSET)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class PostureTests(unittest.TestCase):
    def report(self, directory, events, input_id="input44"):
        with TemporaryFile() as source:
            for kind, code, value in events:
                source.write(reader.EVENT.pack(0, 0, kind, code, value))
            source.seek(0)
            reader.publish_events(source.fileno(), directory, input_id)

    def test_two_folds_and_unfolds_do_not_leave_stale_disabled_state(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            for posture, code in [("disabled", 0x28f), ("enabled", 0x28e)] * 2:
                self.report(directory, [(1, code, 1), (1, code, 0), (0, 0, 0)])
                self.assertEqual((directory / "state").read_text(), f"input44 {posture}\n")

    def test_only_real_posture_presses_publish_state(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            self.report(directory, [(1, 30, 1), (1, 0x28e, 0), (1, 0x28f, 2), (4, 0x28f, 1)])
            self.assertEqual(list(directory.iterdir()), [])
