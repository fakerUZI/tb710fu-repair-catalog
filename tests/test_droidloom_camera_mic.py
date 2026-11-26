import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch


ENTRY = Path(__file__).resolve().parents[1] / "contributions/droidloom-camera-mic/attachments"


class CameraUpdateTests(unittest.TestCase):
    def test_reapply_and_rollback_after_droidloom_rebinds_a_library(self):
        guest = runpy.run_path(str(ENTRY / "install.py"))["guest"]
        with tempfile.TemporaryDirectory() as work:
            spec = Path(work) / "cell.json"
            with patch.dict(guest.__globals__, SPEC=spec, DEST=Path(work) / "payload"):
                service, library = guest.__globals__["TARGETS"]
                stock = {"target": library, "source": "/factory/library"}
                other = {"target": "/unrelated", "source": "/another/repair"}
                spec.write_text(json.dumps({"android_file_overrides": [stock, other]}))
                guest("apply")
                current = json.loads(spec.read_bytes())
                updated = {"target": library, "source": "/updated/library"}
                current["android_file_overrides"] = [
                    updated if e["target"] == library else e for e in current["android_file_overrides"]
                ]
                spec.write_text(json.dumps(current))
                guest("apply")
                guest("apply")
                guest("rollback")
                guest("rollback")
                final = json.loads(spec.read_bytes())["android_file_overrides"]
                self.assertCountEqual(final, [updated, other])
                self.assertNotIn(service, [e["target"] for e in final])
