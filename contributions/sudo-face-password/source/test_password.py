"""Run on Ubuntu with the baseline face-unlock.py installed (camera is stubbed)."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("system_password", Path(__file__).with_name("system-password.py"))
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class PasswordPrompt(unittest.TestCase):
    uid = 1000

    def setUp(self):
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ("MODES", "RUNTIME"):
            path = root / name
            path.mkdir()
            self.stack.enter_context(patch.object(auth, name, path))
        self.stack.enter_context(patch.object(auth.face, "STATE", root))
        self.stack.enter_context(patch.object(auth, "BOOT", root / "boot"))
        self.stack.enter_context(patch.object(auth, "enabled", return_value=True))
        self.stack.enter_context(patch.object(auth, "local_request", return_value=True))
        self.stack.enter_context(patch.object(auth.pwd, "getpwuid", return_value=type("User", (), {"pw_name": "test"})()))
        self.stack.enter_context(patch.dict(os.environ, PKEXEC_UID=str(self.uid)))
        self.desktop = dict(User=(1000, "/"), Name="test", Seat=("seat0", "/"), Class="user",
                            Type="wayland", Service="gdm-password", Remote=False, Active=True, LockedHint=False, Id="2")
        self.stack.enter_context(patch.object(auth.face, "active_session", return_value=self.desktop))
        self.banner = self.stack.enter_context(patch.object(auth, "scan_banner"))
        self.verify = self.stack.enter_context(patch.object(auth.subprocess, "run",
                                                return_value=subprocess.CompletedProcess([], 0)))
        auth.BOOT.write_text("boot-1")
        auth.face.template_path(self.uid).write_text("template")
        self.epoch = "unlock-1"

    def request(self, expected, *, face=True, success=True):
        # Root peers are required by the production socket.
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        errors = []

        def handle():
            with server:
                try:
                    auth.authenticate(server, lambda: self.epoch)
                except Exception as error:
                    errors.append(error)

        thread = threading.Thread(target=handle)
        thread.start()
        with client:
            client.settimeout(2)
            client.sendall(b"sudo 1000 0")
            self.assertEqual(client.recv(16), expected)
            if expected and face:
                client.sendall(b"FACE")
                self.assertEqual(client.recv(16), b"OK" if success else b"")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_unlock(self):
        auth.configure("unlock")
        self.request(b"ASK", face=False)
        self.verify.assert_not_called()
        self.banner.assert_not_called()
        self.request(b"ASK")
        self.request(b"AUTO")
        self.assertEqual(self.verify.call_count, 2)
        self.assertEqual(self.banner.call_count, 2)
        self.banner.return_value.__exit__.assert_called_with(None, None, None)
        self.desktop["LockedHint"] = True
        self.request(b"")
        self.desktop["LockedHint"] = False
        self.epoch = "unlock-2"
        self.request(b"ASK")

    def test_boot_and_always(self):
        for mode in ("boot", "always"):
            auth.configure(mode)
            self.request(b"ASK")
            self.epoch += "lock"
            self.request(b"AUTO")
            auth.BOOT.write_text(mode)
            self.request(b"ASK" if mode == "boot" else b"AUTO")
        auth.configure("off")
        self.request(b"")

    def test_refusal_and_inflight_changes(self):
        self.request(b"")
        auth.configure("always")
        self.verify.return_value.returncode = 1
        self.request(b"ASK", success=False)
        self.verify.return_value.returncode = 0
        self.verify.side_effect = lambda *a, **kw: (setattr(self, "epoch", "relocked")
                                                   or self.verify.return_value)
        self.request(b"ASK", success=False)
        self.verify.side_effect = None
        self.request(b"ASK")
        self.verify.return_value.returncode = 1
        self.request(b"AUTO", success=False)
        self.verify.return_value.returncode = 0
        auth.face.template_path(self.uid).write_text("re-enrolled")
        self.request(b"ASK")
        self.desktop["Remote"] = True
        self.request(b"")


if __name__ == "__main__":
    unittest.main()
