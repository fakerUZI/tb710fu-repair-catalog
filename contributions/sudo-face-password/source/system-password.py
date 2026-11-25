#!/usr/bin/python3 -I
"""System-PAM face verification for the foreground TB710FU desktop account."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
import syslog
import tempfile
import threading
import uuid

from gi.repository import Gio, GLib, GLibUnix

PREFIX = Path("/usr/local/lib/tb710fu-face-unlock")
spec = importlib.util.spec_from_file_location("lock_screen", PREFIX / "face-unlock.py")
face = importlib.util.module_from_spec(spec)
spec.loader.exec_module(face)
MODES = Path("/var/lib/tb710fu-face-password")
RUNTIME = Path("/run/tb710fu-face-password")
BOOT = Path("/proc/sys/kernel/random/boot_id")
RULE = "auth sufficient pam_exec.so type=auth quiet quiet_log stdout seteuid " + str(PREFIX / "pam-face-unlock")
MODES_ALLOWED = ("off", "unlock", "boot", "always")


def atomic_text(path, text, mode):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(text)
            os.fchmod(fd, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def enabled(uid, name):
    return (RULE in Path("/etc/pam.d/gdm-password").read_text().splitlines()
            and face.load_template(uid, name) is not None)


def configure(mode):
    uid = int(os.environ["PKEXEC_UID"])
    name = pwd.getpwuid(uid).pw_name
    if not face.is_local_desktop(face.active_session(), uid, name, False):
        raise ValueError("请在已解锁的本机桌面设置。")
    if mode != "off" and not enabled(uid, name):
        raise ValueError("请先录入人脸并启用人脸解锁。")
    atomic_text(MODES / str(uid), f"{mode}\n{uuid.uuid4().hex}\n", 0o644)


def context(uid, epoch):
    name = pwd.getpwuid(uid).pw_name
    session = face.active_session()
    if not face.is_local_desktop(session, uid, name, False) or not enabled(uid, name):
        return None
    mode, revision = (MODES / str(uid)).read_text().splitlines()
    scope = {"unlock": epoch + ":" + session["Id"], "boot": BOOT.read_text().strip(),
             "always": "always"}.get(mode)
    if scope is None:
        return None
    st = face.template_path(uid).stat()
    key = json.dumps([name, revision, scope, st.st_ino, st.st_ctime_ns])
    return session["Id"], epoch, hashlib.sha256(key.encode()).hexdigest()


def process_is(pid, path):
    return os.readlink(f"/proc/{pid}/exe") == os.path.realpath(path)


def local_request(service, uid, pid, agent, session):
    if service == "polkit-1":
        return (process_is(pid, "/usr/lib/polkit-1/polkit-agent-helper-1")
                and Path(f"/proc/{agent}").stat().st_uid == uid
                and process_is(agent, "/usr/bin/gnome-shell")
                and f"/user@{uid}.service/session.slice/org.gnome.Shell@" in
                Path(f"/proc/{agent}/cgroup").read_text())
    if service not in ("sudo", "sudo-i") or not process_is(pid, "/usr/bin/sudo"):
        return False
    # Field 7 is the controlling tty. Reject background and headless clients.
    stat_fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if int(stat_fields[4]) == 0:
        return False
    if Path(f"/proc/{pid}/loginuid").read_text().strip() != str(uid):
        return False
    # A terminal in the session scope, or GNOME Terminal started by the user manager.
    groups = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    if any(line.endswith(f"/session-{session}.scope") for line in groups):
        return True
    if not any(f"/user@{uid}.service/" in line for line in groups):
        return False
    while pid and pid > 1:
        if process_is(pid, "/usr/libexec/gnome-terminal-server"):
            return Path(f"/proc/{pid}").stat().st_uid == uid
        pid = face.parent_pid(pid)
    return False


@contextmanager
def scan_banner(uid, service):
    # Polkit shows PAM_TEXT_INFO inside its modal. The desktop terminal also
    # gets a transient banner, owned by the user whose face is being checked.
    notice = None
    if service in ("sudo", "sudo-i"):
        runtime = f"/run/user/{uid}"
        try:
            notice = subprocess.Popen(
                ["/usr/bin/python3", "-I", str(PREFIX / "scan-notice.py")],
                user=uid, group=pwd.getpwuid(uid).pw_gid, extra_groups=[],
                env={"XDG_RUNTIME_DIR": runtime, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                     "LANG": "C.UTF-8"},
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass  # The inline prompt is still available.
    try:
        yield
    finally:
        if notice is not None:
            notice.stdin.close()
            try:
                notice.wait(timeout=0.8)
            except subprocess.TimeoutExpired:
                notice.kill()
                notice.wait()


def authenticate(conn, epoch):
    pid, peer_uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer_uid != 0:
        return
    conn.settimeout(2)
    service, user, peer = conn.recv(128).decode("ascii").split()
    uid, agent = int(user), int(peer)
    # The default (including after upgrading) goes straight to the password prompt.
    if not (MODES / str(uid)).is_file():
        return
    before = context(uid, epoch())
    if not before or not local_request(service, uid, pid, agent, before[0]):
        return
    marker = face.STATE / f"password-choice-{uid}"
    remembered = marker.exists() and marker.read_text() == before[2]
    conn.sendall(b"AUTO" if remembered else b"ASK")
    conn.settimeout(90)
    if conn.recv(16) != b"FACE" or context(uid, epoch()) != before:
        return
    # Camera/model initialization must not block the logind signal loop.
    # Bound the capture subprocess even if the camera driver stalls.
    with (RUNTIME / "camera.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with scan_banner(uid, service):
            checked = subprocess.run(["/usr/bin/python3", "-I", __file__, "verify", user],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, timeout=9)
        if checked.returncode or context(uid, epoch()) != before:
            return
        if not local_request(service, uid, pid, agent, before[0]):
            return
        if not remembered:
            atomic_text(marker, before[2], 0o600)
        conn.sendall(b"OK")
        consent = "remembered choice" if remembered else "first choice"
        syslog.syslog(syslog.LOG_AUTHPRIV | syslog.LOG_NOTICE,
                      f"Local {service} face verification succeeded ({consent})")


def serve():
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    generation = uuid.uuid4().hex

    def rotate(*_args):
        nonlocal generation
        generation = uuid.uuid4().hex

    def rotate_session(_bus, _sender, path, *_args):
        # ActiveSession carries the actual logind object path (e.g. /session/_32).
        # A remote session starting or ending must not expire the tablet choice.
        try:
            seat = bus.call_sync(face.LOGIN, "/org/freedesktop/login1/seat/seat0",
                                 "org.freedesktop.DBus.Properties", "Get",
                                 GLib.Variant("(ss)", (face.LOGIN + ".Seat", "ActiveSession")),
                                 None, Gio.DBusCallFlags.NONE, 800, None)
            if seat.unpack()[0][1] == path:
                rotate()
        except GLib.Error:
            rotate()

    def session_change(_bus, _sender, _path, _interface, _signal, parameters):
        interface, changes, invalidated = parameters.unpack()
        if interface == face.LOGIN + ".Session" and {"Active", "LockedHint"}.intersection(changes.keys() | set(invalidated)):
            rotate_session(_bus, _sender, _path)

    bus.signal_subscribe(face.LOGIN, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                         None, face.LOGIN + ".Session", Gio.DBusSignalFlags.NONE, session_change)
    for signal in ("Lock", "Unlock"):
        bus.signal_subscribe(face.LOGIN, face.LOGIN + ".Session", signal, None, None,
                             Gio.DBusSignalFlags.NONE, rotate_session)
    bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                         "/org/freedesktop/DBus", face.LOGIN, Gio.DBusSignalFlags.NONE, rotate)
    slots = threading.BoundedSemaphore(8)

    def handle(conn):
        try:
            with conn:
                authenticate(conn, lambda: generation)
        except (OSError, GLib.Error, ValueError, KeyError, subprocess.SubprocessError):
            pass  # A disconnected client or unavailable camera falls back to PAM.
        finally:
            slots.release()

    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
        address = RUNTIME / "auth.sock"
        address.unlink(missing_ok=True)
        server.bind(str(address))
        server.listen(8)

        def accept(_fd, _condition):
            conn, _ = server.accept()
            if slots.acquire(blocking=False):
                threading.Thread(target=handle, args=(conn,), daemon=True).start()
            else:
                conn.close()
            return True

        GLibUnix.fd_add_full(GLib.PRIORITY_DEFAULT, server.fileno(), GLib.IOCondition.IN, accept)
        loop.run()


def main():
    parser = argparse.ArgumentParser()
    command = parser.add_subparsers(dest="command", required=True)
    command.add_parser("serve")
    command.add_parser("configure").add_argument("mode", choices=MODES_ALLOWED)
    command.add_parser("verify").add_argument("uid", type=int)
    args = parser.parse_args()
    if os.geteuid() != 0:
        return 1
    if args.command == "serve":
        serve()
    elif args.command == "configure":
        configure(args.mode)
    else:
        name = pwd.getpwuid(args.uid).pw_name
        template = face.load_template(args.uid, name)
        return 0 if template and face.verify(template) else 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, GLib.Error) as error:
        sys.exit(str(error))
