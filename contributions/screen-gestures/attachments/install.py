#!/usr/bin/python3
"""Install the TB710FU screen gestures and guard their first boot."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
import time


KERNEL = "7.2.6-tb710fu-20261118"
BASE = Path("/var/backups/tb710fu-screen-gestures")
DRIVERS = Path("/lib/modules", KERNEL, "updates/tb710fu-screen-gestures")
UNIT = "tb710fu-screen-gestures-guard.service"
USER_UNIT = "tb710fu-double-tap-wake.service"
DING = Path("/usr/share/gnome-shell/extensions/ding@rastersoft.com/app/desktopGrid.js")
PERMISSIONS = Path("/usr/local/sbin/tb-session-permissions.py")
SCHEMAS = Path("/usr/share/glib-2.0/schemas")
CONTROL = Path("/sys/bus/mipi-dsi/devices/ae94000.dsi.0/dt2w_enabled")
SETTINGS_BIN = Path("/usr/local/libexec/tb710fu-screen-gestures-settings")
SETTINGS_HELPER = Path("/usr/local/libexec/tb710fu-screen-gestures-settings-user.py")
SYSTEMCTL = "/usr/bin/systemctl"
TOUCH = "nvt_36xxx"
PANEL = "panel_nt36532_wqhd_dual_dsi_dsc"
DESTINATIONS = {
    "nvt_36xxx.ko": (DRIVERS / "nvt_36xxx.ko", 0o644),
    "panel-nt36532-wqhd-dual-dsi-dsc.ko": (DRIVERS / "panel-nt36532-wqhd-dual-dsi-dsc.ko", 0o644),
    "desktopGrid.js": (DING, 0o644),
    "tb-session-permissions.py": (PERMISSIONS, 0o755),
    "org.tb710fu.screen-gestures.gschema.xml": (SCHEMAS / "org.tb710fu.screen-gestures.gschema.xml", 0o644),
    "double-tap-wake.py": (Path("/usr/local/libexec/tb710fu-double-tap-wake"), 0o755),
    USER_UNIT: (Path("/etc/systemd/user", USER_UNIT), 0o644),
    "gnome-control-center": (SETTINGS_BIN, 0o755),
    "settings-user.py": (SETTINGS_HELPER, 0o644),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args):
    command = subprocess.run(args, capture_output=True, text=True, timeout=60)
    require(command.returncode == 0, f"{' '.join(args)} failed: {command.stderr.strip()}")
    return command.stdout.strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def build_id(module):
    note = Path("/sys/module", module, "notes/.note.gnu.build-id")
    return note.read_bytes()[16:].hex() if note.is_file() else ""


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def put(path, data, mode):
    fd, name = tempfile.mkstemp(prefix=".tb710fu-gestures-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def verify_firmware(spec):
    require(b"lenovo,tb710fu" in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"),
            "Expected Lenovo TB710FU")
    release = Path("/etc/os-release").read_text().splitlines()
    require("ID=ubuntu" in release and any(line in ("VERSION_ID=26.04", 'VERSION_ID="26.04"')
                                            for line in release)
            and os.uname().machine == "aarch64", "Ubuntu 26.04 ARM64 is required")
    require("androidboot.slot_suffix=_a" in Path("/proc/cmdline").read_text().split(),
            "Unexpected firmware slot")
    config = gzip.decompress(Path("/proc/config.gz").read_bytes())
    require(sha(config) == spec["config_sha256"], "Running kernel configuration changed")

    disks = sorted(Path("/sys/class/block").glob("sd?"))
    require(len(disks) == 6, "Expected six UFS LUNs")
    labels = {}
    for disk in disks:
        table = json.loads(run("/usr/sbin/sfdisk", "--json", f"/dev/{disk.name}"))["partitiontable"]
        require(table["label"] == "gpt" and table.get("partitions"), f"Invalid GPT on {disk.name}")
        for partition in table["partitions"]:
            labels.setdefault(partition.get("name"), []).append(Path(partition["node"]))
    for label in ("boot_a", *spec["firmware_sha256"]):
        node = Path("/dev/disk/by-partlabel", label).resolve(strict=True)
        require(labels.get(label) == [node] and stat.S_ISBLK(node.stat().st_mode),
                f"GPT label does not match {label}")
        if label == "boot_a":
            with node.open("rb") as image:
                require(image.read(8) == b"ANDROID!", "Unexpected active boot image format")
        else:
            require(file_sha(node) == spec["firmware_sha256"][label], f"Firmware changed for {label}")


def verify_baseline(spec):
    for module, expected in spec["originals"].items():
        installed = Path(run("/usr/sbin/modinfo", "-F", "filename", module))
        require(build_id(module) == expected["id"] and file_sha(installed) == expected["sha256"],
                f"Running or installed baseline changed for {module}")
    for package, version in spec["packages"].items():
        require(run("/usr/bin/dpkg-query", "-W", "-f=${Version}", package) == version,
                f"Package version changed for {package}")
    for path, digest in spec["edits"].items():
        file = Path(path)
        require(not file.is_symlink() and file_sha(file) == digest, f"Installed file changed: {path}")


def desktop_account():
    session = run("/usr/bin/loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value")
    require(session, "Open the repair assistant from the local Ubuntu desktop")
    props = dict(line.split("=", 1) for line in run(
        "/usr/bin/loginctl", "show-session", session, "-p", "User", "-p", "Class",
        "-p", "Active", "-p", "Type", "-p", "Seat",
    ).splitlines())
    require(props.get("Class") == "user" and props.get("Active") == "yes"
            and props.get("Seat") == "seat0" and props.get("Type") == "wayland",
            "The local GNOME Wayland desktop must be active")
    uid = int(props["User"])
    caller = os.environ.get("PKEXEC_UID", os.environ.get("SUDO_UID", str(uid)))
    require(uid > 0 and uid == int(caller), "The caller differs from the active desktop user")
    return pwd.getpwuid(uid).pw_name


def user_settings(account, action):
    info = pwd.getpwnam(account)
    runtime = f"/run/user/{info.pw_uid}"
    args = (str(SETTINGS_BIN),) if action == "apply" else ()
    return run("/usr/sbin/runuser", "-u", account, "--", "/usr/bin/env", f"HOME={info.pw_dir}",
               f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
               "/usr/bin/python3", "-I", str(SETTINGS_HELPER), action, *args)


def verify_payload(here, spec):
    replacements = {}
    for name, (target, mode) in DESTINATIONS.items():
        source = here / name
        data = source.read_bytes()
        require(sha(data) == spec["files"][name], f"Payload verification failed for {name}")
        if name.endswith(".ko"):
            require(run("/usr/sbin/modinfo", "-F", "vermagic", str(source)).startswith(KERNEL + " "),
                    f"Wrong module ABI for {name}")
        replacements[target] = (data, mode)
    return replacements


def indexes():
    run("/usr/sbin/depmod", "-a", KERNEL)
    run("/usr/bin/glib-compile-schemas", "--strict", str(SCHEMAS))


def clean_units():
    if Path("/etc/systemd/system", UNIT).is_file():
        run(SYSTEMCTL, "disable", UNIT)
    if Path("/etc/systemd/user", USER_UNIT).is_file():
        run(SYSTEMCTL, "--global", "disable", USER_UNIT)


def rollback():
    state = json.loads((BASE / "state.json").read_text())
    for name, entry in state["files"].items():
        path = Path(name)
        acceptable = {entry["sha256"], entry.get("original_sha256")}
        require(not path.is_symlink() and (not path.exists() or file_sha(path) in acceptable),
                f"File changed since installation: {path}")
        if "original" in entry:
            require(file_sha(BASE / entry["original"]) == entry["original_sha256"],
                    f"Original backup changed: {path}")

    if state.get("settings_account") and SETTINGS_HELPER.is_file():
        user_settings(state["settings_account"], "rollback")
    clean_units()
    for name, entry in state["files"].items():
        path = Path(name)
        if "original" in entry:
            put(path, (BASE / entry["original"]).read_bytes(), entry["original_mode"])
        else:
            path.unlink(missing_ok=True)
    if DRIVERS.exists():
        try:
            DRIVERS.rmdir()
        except OSError:
            pass
    indexes()
    run(SYSTEMCTL, "daemon-reload")
    retired = BASE.with_name(f"{BASE.name}-rolled-back-{time.time_ns()}")
    BASE.rename(retired)
    print("原驱动与桌面处理已恢复。请保存工作后重启。", flush=True)


def install(check=False):
    here = Path(__file__).resolve().parent
    spec = json.loads((here / "spec.json").read_text())
    replacements = verify_payload(here, spec)
    verify_firmware(spec)
    if BASE.is_dir():
        state = json.loads((BASE / "state.json").read_text())
        require(state["new_build_ids"] == spec["new_build_ids"], "A different screen gestures build is installed")
        for name, entry in state["files"].items():
            require(file_sha(Path(name)) == entry["sha256"], f"Installed file changed: {name}")
        print("屏幕手势已安装。可在「设置 → 隐私与安全 → 锁屏」分别启用。", flush=True)
        return
    verify_baseline(spec)
    account = desktop_account()
    rule = "".join(f"override {module} {KERNEL} updates/tb710fu-screen-gestures\n"
                   for module in (TOUCH, PANEL))
    replacements[Path("/etc/depmod.d/95-tb710fu-screen-gestures.conf")] = (rule.encode(), 0o644)
    replacements[Path("/etc/systemd/system", UNIT)] = (f"""[Unit]
Description=Verify TB710FU screen gestures on the first boot
After=local-fs.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 -I {BASE}/install.py guard
[Install]
WantedBy=multi-user.target
""".encode(), 0o644)
    for path in (BASE, DRIVERS, *replacements):
        if str(path) not in spec["edits"]:
            require(not path.exists() and not path.is_symlink(), f"Unexpected existing file: {path}")

    print("Verified current GPT, firmware, drivers, and Ubuntu desktop packages.", flush=True)
    if check:
        return
    BASE.mkdir(mode=0o700)
    files = {}
    state = {"staged_boot": boot_id(), "files": files, "new_build_ids": spec["new_build_ids"],
             "settings_account": account}
    for number, (path, (data, _mode)) in enumerate(replacements.items()):
        entry = {"sha256": sha(data)}
        if str(path) in spec["edits"]:
            old = path.read_bytes()
            require(sha(old) == spec["edits"][str(path)], f"Original changed during staging: {path}")
            entry.update(original=f"original-{number}", original_sha256=sha(old),
                         original_mode=stat.S_IMODE(path.stat().st_mode))
            put(BASE / entry["original"], old, 0o600)
        files[str(path)] = entry
    put(BASE / "state.json", json.dumps(state).encode(), 0o600)
    put(BASE / "install.py", Path(__file__).read_bytes(), 0o700)
    try:
        DRIVERS.mkdir(parents=True)
        for path, (data, mode) in replacements.items():
            put(path, data, mode)
        indexes()
        for module, name in ((TOUCH, "nvt_36xxx.ko"), (PANEL, "panel-nt36532-wqhd-dual-dsi-dsc.ko")):
            selected = Path(run("/usr/sbin/modinfo", "-k", KERNEL, "-F", "filename", module))
            require(selected.resolve() == (DRIVERS / name).resolve(), f"depmod did not select {module}")
        run(SYSTEMCTL, "daemon-reload")
        run(SYSTEMCTL, "enable", UNIT)
        run(SYSTEMCTL, "--global", "enable", USER_UNIT)
        user_settings(account, "apply")
        os.sync()
    except Exception:
        rollback()
        raise
    print("屏幕手势已安装。请保存工作后重启，再在「设置 → 隐私与安全 → 锁屏」启用。", flush=True)


def guard():
    state = json.loads((BASE / "state.json").read_text())
    if state["staged_boot"] == boot_id():
        return
    deadline = time.monotonic() + 120
    parameters = Path("/sys/module", TOUCH, "parameters")
    while time.monotonic() < deadline:
        loaded = all(build_id(module) == note for module, note in state["new_build_ids"].items())
        if loaded and CONTROL.is_file() and (parameters / "panel_resumes").is_file():
            if (int((parameters / "panel_resumes").read_text()) and
                    int((parameters / "panel_resume_result").read_text()) == 0):
                run(SYSTEMCTL, "disable", UNIT)
                print("Screen gesture modules and synchronous touch resume verified.", flush=True)
                return
        time.sleep(1)
    print("Screen gesture driver verification timed out. Reverting.", flush=True)
    rollback()
    run(SYSTEMCTL, "reboot", "--no-block")


if __name__ == "__main__":
    require(os.geteuid() == 0 and os.uname().release == KERNEL, "Expected root on the verified kernel")
    match sys.argv[1:]:
        case ["apply"]:
            install()
        case ["check"]:
            install(check=True)
        case ["guard"]:
            guard()
        case ["rollback"]:
            rollback()
        case _:
            raise SystemExit("usage: install.py check|apply|guard|rollback")
