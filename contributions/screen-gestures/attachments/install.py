#!/usr/bin/python3
"""Install the TB710FU screen gestures and guard their first boot."""
import fcntl
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
LAUNCHER_FILES = ("gesture-settings.py", "tb710fu-gesture-settings.desktop")
DING = Path("/usr/share/gnome-shell/extensions/ding@rastersoft.com/app/desktopGrid.js")
PERMISSIONS = Path("/usr/local/sbin/tb-session-permissions.py")
SCHEMAS = Path("/usr/share/glib-2.0/schemas")
TAPS = Path("/sys/module/nvt_36xxx/parameters/dt2w_taps")
CONTROL = Path("/sys/bus/mipi-dsi/devices/ae94000.dsi.0/dt2w_enabled")
SETTINGS_BIN = Path("/usr/local/libexec/tb710fu-screen-gestures-settings")
SETTINGS_HELPER = Path("/usr/local/libexec/tb710fu-screen-gestures-settings-user.py")
SYSTEMCTL = "/usr/bin/systemctl"
TOUCH = "nvt_36xxx"
PANEL = "panel_nt36532_wqhd_dual_dsi_dsc"
DESTINATIONS = {
    "nvt_36xxx.ko": (DRIVERS / "nvt_36xxx.ko", 0o644),
    "panel-nt36532-wqhd-dual-dsi-dsc.ko": (DRIVERS / "panel-nt36532-wqhd-dual-dsi-dsc.ko", 0o644),
    "org.tb710fu.screen-gestures.gschema.xml": (SCHEMAS / "org.tb710fu.screen-gestures.gschema.xml", 0o644),
    "double-tap-wake.py": (Path("/usr/local/libexec/tb710fu-double-tap-wake"), 0o755),
    USER_UNIT: (Path("/etc/systemd/user", USER_UNIT), 0o644),
    "gnome-control-center": (SETTINGS_BIN, 0o755),
    "settings-user.py": (SETTINGS_HELPER, 0o644),
    "gesture-settings.py": (Path("/usr/local/libexec/tb710fu-gesture-settings"), 0o755),
    "tb710fu-gesture-settings.desktop": (Path("/usr/local/share/applications/tb710fu-gesture-settings.desktop"), 0o644),
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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def needs_modules(spec):
    if CONTROL.is_file() and TAPS.is_file():
        return False
    require(os.uname().release == KERNEL,
            f"当前内核缺少双击亮屏接口，附件仅对应 {KERNEL}")
    for module, expected in spec["originals"].items():
        installed = Path(run("/usr/sbin/modinfo", "-F", "filename", module))
        require(build_id(module) == expected["id"] and file_sha(installed) == expected["sha256"],
                f"{module} 当前驱动版本与替换附件的基线不同")
    return True


def has_settings_version(spec):
    for package, version in spec["packages"].items():
        result = subprocess.run(("/usr/bin/dpkg-query", "-W", "-f=${Version}", package),
                                capture_output=True, text=True, timeout=5)
        if result.returncode != 0 or result.stdout.strip() != version:
            return False
    return True


def patched(data, patch):
    # Match each hunk with its exact context so edits outside the gesture code survive.
    hunks = []
    for line in patch.splitlines(keepends=True):
        if line.startswith(b"@@ "):
            hunks.append(([], []))
        elif hunks:
            old, new = hunks[-1]
            if line[:1] in (b" ", b"-"):
                old.append(line[1:])
            if line[:1] in (b" ", b"+"):
                new.append(line[1:])
    for old_lines, new_lines in hunks:
        old, new = b"".join(old_lines), b"".join(new_lines)
        if data.count(new) == 1:
            continue
        require(data.count(old) == 1, "找不到原有手势代码接口，或该处已被其他补丁修改")
        data = data.replace(old, new, 1)
    return data


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


def verify_payload(here, spec, drivers, native_settings, names=DESTINATIONS):
    replacements = {}
    for name in names:
        target, mode = DESTINATIONS[name]
        if (name.endswith(".ko") and not drivers
                or name in ("gnome-control-center", "settings-user.py") and not native_settings):
            continue
        source = here / name
        data = source.read_bytes()
        require(sha(data) == spec["files"][name], f"Payload verification failed for {name}")
        if name.endswith(".ko"):
            require(run("/usr/sbin/modinfo", "-F", "vermagic", str(source)).startswith(KERNEL + " "),
                    f"Wrong module ABI for {name}")
        replacements[target] = (data, mode)
    return replacements


def indexes(drivers):
    if drivers:
        run("/usr/sbin/depmod", "-a", KERNEL)
    run("/usr/bin/glib-compile-schemas", "--strict", str(SCHEMAS))


def global_wake_enabled():
    result = subprocess.run((SYSTEMCTL, "--global", "is-enabled", USER_UNIT),
                            capture_output=True, text=True, timeout=5)
    return result.stdout.strip() == "enabled"


def clean_units(state):
    if state.get("drivers", True) and Path("/etc/systemd/system", UNIT).is_file():
        run(SYSTEMCTL, "disable", UNIT)
    if not state.get("wake_was_enabled") and Path("/etc/systemd/user", USER_UNIT).is_file():
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
    clean_units(state)
    for name, entry in state["files"].items():
        path = Path(name)
        if "original" in entry:
            put(path, (BASE / entry["original"]).read_bytes(), entry["original_mode"])
        else:
            path.unlink(missing_ok=True)
    drivers = state.get("drivers", True)
    if drivers and DRIVERS.exists():
        try:
            DRIVERS.rmdir()
        except OSError:
            pass
    indexes(drivers)
    run(SYSTEMCTL, "daemon-reload")
    retired = BASE.with_name(f"{BASE.name}-rolled-back-{time.time_ns()}")
    BASE.rename(retired)
    print("原驱动与桌面处理已恢复。请保存工作后重启。", flush=True)


def install(check=False):
    here = Path(__file__).resolve().parent
    spec = json.loads((here / "spec.json").read_text())
    require(not BASE.is_symlink(), "手势备份路径异常")
    if BASE.is_dir():
        state = json.loads((BASE / "state.json").read_text())
        for name, entry in state["files"].items():
            path = Path(name)
            # Package updates can change DING and unrelated power permissions code.
            if path in (DING, PERMISSIONS):
                continue
            require(path.is_file() and file_sha(path) == entry["sha256"], f"手势安装文件有变化：{name}")
        for path in (DING, PERMISSIONS):
            require(gesture_edit(here, spec, path)[0] == path.read_bytes(),
                    f"已安装的手势代码被其他更新覆盖：{path}")
        launchers = verify_payload(here, spec, False, False, LAUNCHER_FILES)
        missing = {path: payload for path, payload in launchers.items()
                   if str(path) not in state["files"]}
        for path, (data, _mode) in missing.items():
            require(not path.is_symlink() and (not path.exists() or path.read_bytes() == data),
                    f"已有其他程序使用此路径：{path}")
        if missing and not check:
            add_launchers(state, missing)
        print("屏幕手势检查通过。可在「设置 → 隐私与安全 → 锁屏」或应用菜单中的「屏幕手势」操作。",
              flush=True)
        return

    drivers = needs_modules(spec)
    native_settings = has_settings_version(spec)
    account = desktop_account()
    require(os.access("/usr/local/bin/tb-backlight-power", os.X_OK), "缺少双击亮屏所需的背光控制")
    run("/usr/bin/python3", "-I", "-c",
        "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gio, Gtk")
    replacements = verify_payload(here, spec, drivers, native_settings)
    for path in (DING, PERMISSIONS):
        replacements[path] = gesture_edit(here, spec, path)
    if drivers:
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
    for path in replacements:
        require(not path.is_symlink() and (not path.exists() or path.is_file()), f"异常安装路径：{path}")

    print("屏幕手势的触控接口、驱动与桌面组件检查通过。", flush=True)
    if check:
        return
    files = {}
    state = {"staged_boot": boot_id(), "files": files, "new_build_ids": spec["new_build_ids"],
             "wake_was_enabled": global_wake_enabled(), "drivers": drivers}
    BASE.mkdir(mode=0o700, parents=True)
    try:
        for number, (path, (data, _mode)) in enumerate(replacements.items()):
            entry = {"sha256": sha(data)}
            if path.exists():
                old = path.read_bytes()
                entry.update(original=f"original-{number}", original_sha256=sha(old),
                             original_mode=stat.S_IMODE(path.stat().st_mode))
                put(BASE / entry["original"], old, 0o600)
            files[str(path)] = entry
        put(BASE / "state.json", json.dumps(state).encode(), 0o600)
        put(BASE / "install.py", Path(__file__).read_bytes(), 0o700)
    except Exception:
        for path in BASE.iterdir():
            path.unlink()
        BASE.rmdir()
        raise
    try:
        if drivers:
            DRIVERS.mkdir(parents=True, exist_ok=True)
        for path, (data, mode) in replacements.items():
            put(path, data, mode)
        indexes(drivers)
        if drivers:
            for module, name in ((TOUCH, "nvt_36xxx.ko"), (PANEL, "panel-nt36532-wqhd-dual-dsi-dsc.ko")):
                selected = Path(run("/usr/sbin/modinfo", "-k", KERNEL, "-F", "filename", module))
                require(selected.resolve() == (DRIVERS / name).resolve(), f"depmod did not select {module}")
            run(SYSTEMCTL, "daemon-reload")
            run(SYSTEMCTL, "enable", UNIT)
        run(SYSTEMCTL, "--global", "enable", USER_UNIT)
        if native_settings:
            try:
                if user_settings(account, "apply") == "installed":
                    state["settings_account"] = account
                    put(BASE / "state.json", json.dumps(state).encode(), 0o600)
            except RuntimeError as error:
                print(f"已有或无法更新的 GNOME 设置入口，请使用应用菜单：{error}", flush=True)
        os.sync()
    except Exception:
        rollback()
        raise
    print("屏幕手势已安装。请保存工作后重启，再打开应用菜单中的「屏幕手势」分别启用。", flush=True)


def add_launchers(state, launchers):
    created = []
    try:
        for path, (data, mode) in launchers.items():
            if path.exists():
                continue
            put(path, data, mode)
            created.append(path)
            state["files"][str(path)] = {"sha256": sha(data)}
        put(BASE / "state.json", json.dumps(state).encode(), 0o600)
    except OSError:
        for path in created:
            path.unlink()
        raise


def gesture_edit(here, spec, path):
    require(path.is_file() and not path.is_symlink(), f"缺少桌面组件：{path}")
    name = {DING: "desktop-icons.patch", PERMISSIONS: "session-permissions.patch"}[path]
    patch = (here / name).read_bytes()
    require(sha(patch) == spec["files"][name], f"附件验证失败：{name}")
    data = path.read_bytes()
    # Recognize the previous catalog payload directly as well as exact hunks.
    baseline = {DING: "desktopGrid.js", PERMISSIONS: "tb-session-permissions.py"}[path]
    if sha(data) != spec["files"][baseline]:
        data = patched(data, patch)
    return data, stat.S_IMODE(path.stat().st_mode)


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
    require(os.geteuid() == 0, "需要管理员权限")
    action, = sys.argv[1:]
    try:
        if action == "check":
            install(check=True)
        else:
            with open("/run/lock/tb710fu-screen-gestures.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                match action:
                    case "apply":
                        install()
                    case "guard":
                        guard()
                    case "rollback":
                        rollback()
                    case _:
                        raise RuntimeError("usage: install.py check|apply|guard|rollback")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"屏幕手势：{error}") from None
