#!/usr/bin/env python3
"""Configure one TB710FU DP monitor through GNOME's DisplayConfig session API."""

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

INTERNAL = "DSI-1"
INTERFACE = "org.gnome.Mutter.DisplayConfig"
OBJECT = "/org/gnome/Mutter/DisplayConfig"
SIGNATURE = "(uua(iiduba(ssa{sv}))a{sv})"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def native_session():
    uid = os.getuid()
    require(uid != 0, "请以普通 GNOME 桌面用户运行，取消管理员权限。")
    compatible = Path("/proc/device-tree/compatible").read_bytes().split(b"\0")
    require(b"lenovo,tb710fu" in compatible, "设备型号与此条目不符。")
    version = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    require(version.get("ID", "").strip('"') == "ubuntu", "此条目适用于 Ubuntu。")
    active = subprocess.check_output(
        ["/usr/bin/loginctl", "show-seat", "seat0", "--property=ActiveSession", "--value"],
        text=True, timeout=4,
    ).strip()
    require(bool(active), "请先登录平板上的 GNOME 桌面。")
    session = subprocess.check_output(
        ["/usr/bin/loginctl", "show-session", active, "-p", "User", "-p", "Type", "-p", "Class"],
        text=True, timeout=4,
    )
    session = dict(line.split("=", 1) for line in session.splitlines() if "=" in line)
    require(session == {"User": str(uid), "Type": "wayland", "Class": "user"},
            "请从当前本地图形会话以普通用户运行。")
    return Path("/run/user") / str(uid)


class Mutter:
    def __init__(self, runtime):
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.DBusConnection.new_for_address_sync(
            f"unix:path={runtime / 'bus'}",
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )

    def call(self, method, arguments=None):
        return self.bus.call_sync(
            INTERFACE, OBJECT, INTERFACE, method, arguments, None,
            self.Gio.DBusCallFlags.NONE, 8000, None,
        ).unpack()

    def state(self):
        return self.call("GetCurrentState")

    def apply(self, serial, method, layout, layout_mode):
        configuration = []
        for x, y, scale, transform, primary, outputs in layout:
            settings = []
            for connector, mode, props in outputs:
                values = {key: self.GLib.Variant(kind, value) for key, (kind, value) in props.items()}
                settings.append((connector, mode, values))
            configuration.append((x, y, scale, transform, primary, settings))
        properties = {"layout-mode": self.GLib.Variant("u", layout_mode)}
        self.call("ApplyMonitorsConfig", self.GLib.Variant(
            SIGNATURE, (serial, method, configuration, properties)
        ))


def parse_profiles(data):
    root = ET.fromstring(data) if data else ET.Element("monitors", version="2")
    require(root.tag == "monitors" and root.get("version") == "2", "无法识别 GNOME 显示配置。")
    profiles = {}
    for config in root.findall("configuration"):
        modes = {}
        for monitor in config.findall("logicalmonitor/monitor"):
            spec = tuple(monitor.findtext(f"monitorspec/{key}", "") for key in
                         ("connector", "vendor", "product", "serial"))
            require(spec not in modes, "GNOME 配置中存在重复显示器。")
            modes[spec] = (int(monitor.findtext("mode/width", "0")),
                           int(monitor.findtext("mode/height", "0")),
                           float(monitor.findtext("mode/rate", "0")))
        # Separate profiles for the same hardware are safe when they agree on modes.
        if modes:
            profiles.setdefault(frozenset(modes), []).append(modes)
    return profiles


def rate_matches(mode, wanted):
    return mode[:2] == wanted[:2] and abs(mode[2] - wanted[2]) < 0.005


def agrees(profiles, wanted):
    matches = profiles.get(frozenset(wanted), [])
    return bool(matches) and all(
        all(rate_matches(profile[spec], mode) for spec, mode in wanted.items())
        for profile in matches
    )


def current_modes(state):
    return {spec[0]: mode[0] for spec, modes, _ in state[1]
            for mode in modes if mode[-1].get("is-current")}


def single_screen_mode(profiles, spec):
    single = profiles.get(frozenset((spec,)), [])
    require(single and all(rate_matches(entry[spec], single[0][spec]) for entry in single),
            "请先拔掉 DP，在 GNOME 显示设置中保存希望拔线后恢复的单屏刷新率。")
    return single[0][spec]


def build_plan(state, profiles):
    serial, monitors, logical, options = state
    names = {spec[0] for spec, _, _ in monitors}
    ports = [name for name in names if re.fullmatch(r"DP-[0-9]+", name)]
    require(len(monitors) == 2 and len(ports) == 1 and names == {INTERNAL, ports[0]},
            "请只连接一台 DP 外屏，并启用内屏。")
    require(len(logical) == 2 and all(len(row[5]) == 1 for row in logical) and
            sum(row[4] for row in logical) == 1, "请在显示设置中选“加入显示”，再运行本条目。")
    selected = {}
    wanted = {}
    monitor_properties = {}
    for spec, modes, props in monitors:
        connector = spec[0]
        active = next((mode for mode in modes if mode[-1].get("is-current")), None)
        require(active is not None, "两块显示器都应在当前桌面启用。")
        if connector == INTERNAL:
            original = single_screen_mode(profiles, spec)
            resolution, rate, tolerance = (3200, 2000), 120.0, 0.01
        else:
            resolution, rate, tolerance = active[1:3], 60.0, 0.11
        eligible = [mode for mode in modes
                    if mode[1:3] == resolution and abs(mode[3] - rate) <= tolerance]
        require(bool(eligible), f"{connector} 的目标分辨率缺少可用的 {rate:g}Hz 模式。")
        mode = active if active in eligible else min(eligible, key=lambda item: abs(item[3] - rate))
        selected[connector] = mode
        wanted[spec] = (mode[1], mode[2], mode[3])
        monitor_properties[connector] = props
    layout = []
    for x, y, scale, transform, primary, specs, _ in logical:
        connector = specs[0][0]
        props = monitor_properties[connector]
        settings = {}
        if "is-underscanning" in props:
            settings["underscanning"] = ("b", props["is-underscanning"])
        if "color-mode" in props:
            settings["color-mode"] = ("u", props["color-mode"])
        mode = selected[connector]
        require(any(abs(scale - value) < 0.0001 for value in mode[5]),
                f"{connector} 的目标模式不支持当前缩放比例。")
        layout.append((x, y, scale, transform, primary, [(connector, mode[0], settings)]))
    require("layout-mode" in options, "GNOME 未提供当前显示布局。")
    return serial, layout, options["layout-mode"], wanted, original, selected


def read_local(path, limit):
    if not path.exists() and not path.is_symlink():
        return b""
    require(not path.is_symlink() and path.is_file() and path.stat().st_uid == os.getuid(),
            "显示配置或回退记录的文件类型、归属异常。")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "显示配置或回退记录超出合理大小。")
    return data


def config_bytes(path):
    return read_local(path, 1024 * 1024)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-",
                                     delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def save_receipt(path, before, after):
    data = {"before": base64.b64encode(before).decode("ascii"), "installed": after}
    atomic_write(path, (json.dumps(data) + "\n").encode())


@contextmanager
def exclusive(runtime):
    lock = runtime / "tb710fu-dp-refresh-profiles.lock"
    fd = os.open(lock, os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有一个显示配置操作正在运行。") from None
        yield


def apply_profile(mutter, config, receipt):
    before = config_bytes(config)
    original_profiles = parse_profiles(before)
    state = mutter.state()
    serial, layout, layout_mode, wanted, original, selected = build_plan(state, original_profiles)
    print(f"单屏恢复 {original[2]:.3f}Hz，双屏内屏 120Hz、DP 外屏约 60Hz。", flush=True)
    requested = {connector: mode[0] for connector, mode in selected.items()}
    already_saved = agrees(original_profiles, wanted)
    if already_saved and current_modes(state) == requested:
        print("保存配置与当前模式均符合要求。")
        return
    mutter.apply(serial, 0, layout, layout_mode)  # VERIFY
    if already_saved:
        mutter.apply(serial, 1, layout, layout_mode)  # TEMPORARY
        require(current_modes(mutter.state()) == requested, "GNOME 尚未切到目标模式。")
        print("已应用现有保存配置。")
        return

    require(not receipt.exists() and not receipt.is_symlink(),
            "已有本条目的回退记录，请先核对它或在 GNOME 显示设置中调整。")
    receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not receipt.parent.is_symlink() and receipt.parent.stat().st_uid == os.getuid() and
            not (stat.S_IMODE(receipt.parent.stat().st_mode) & 0o077),
            "回退目录必须为当前用户私有目录。")
    require(config_bytes(config) == before, "显示配置同时被改动，请重试。")
    save_receipt(receipt, before, None)
    print("请在 GNOME 的显示确认弹窗点击“保留更改”。", flush=True)
    mutter.apply(serial, 2, layout, layout_mode)  # PERSISTENT, GNOME requests confirmation
    deadline = time.monotonic() + 32
    target = frozenset(wanted)
    untouched = {key: value for key, value in original_profiles.items() if key != target}
    while time.monotonic() < deadline:
        saved = config_bytes(config)
        if saved != before:
            profiles = parse_profiles(saved)
            require({key: value for key, value in profiles.items() if key != target} == untouched,
                    "其他显示配置同时发生变化，已保留私有备份。")
            if agrees(profiles, wanted) and current_modes(mutter.state()) == requested:
                save_receipt(receipt, before, sha(saved))
                print("已核对 GNOME 保存的双屏配置和原单屏刷新率。")
                return
        time.sleep(0.25)
    if config_bytes(config) == before:
        receipt.unlink(missing_ok=True)
    raise RuntimeError("GNOME 没有在确认窗口内保存目标配置。")


def rollback(config, receipt):
    raw = read_local(receipt, 2 * 1024 * 1024)
    if not raw:
        print("此账号没有本条目写入的配置记录。")
        return
    saved = json.loads(raw)
    require(saved["installed"] and sha(config_bytes(config)) == saved["installed"],
            "GNOME 配置后来有变或上次写入未完成，请在显示设置中手动调整。")
    original = base64.b64decode(saved["before"], validate=True)
    require(bool(original), "回退记录缺少原本的单屏配置。")
    parse_profiles(original)
    atomic_write(config, original)
    receipt.unlink()
    print("已恢复此条目前的配置，注销并重新登录后生效。")


def check_profile(mutter, config):
    profiles = parse_profiles(config_bytes(config))
    state = mutter.state()
    monitors = state[1]
    if len(monitors) == 1 and monitors[0][0][0] == INTERNAL:
        internal = monitors[0][0]
        original = single_screen_mode(profiles, internal)
        count = sum(
            1 for specs, entries in profiles.items()
            if internal in specs and len(specs) == 2
            and any(re.fullmatch(r"DP-[0-9]+", spec[0]) for spec in specs)
            and all(rate_matches(entry[internal], (3200, 2000, 120.0)) for entry in entries)
        )
        print(f"单屏保存 {original[2]:.3f}Hz；已有 {count} 套内屏 120Hz 的 DP 双屏配置。")
        print("外屏未连接，连接后可核对当前显示器的配对。")
        return
    _, _, _, wanted, original, _ = build_plan(state, profiles)
    print(f"单屏保存 {original[2]:.3f}Hz，目标内屏双屏 120Hz、DP 外屏约 60Hz。")
    print("双屏配置已保存。" if agrees(profiles, wanted) else "双屏配置待保存。")


def main(action):
    runtime = native_session()
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "monitors.xml"
    receipt = (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) /
               "tb710fu-dp-refresh-profiles" / "rollback.json")
    if action == "check":
        check_profile(Mutter(runtime), config)
    elif action in ("apply", "rollback"):
        with exclusive(runtime):
            if action == "apply":
                apply_profile(Mutter(runtime), config, receipt)
            else:
                rollback(config, receipt)
    else:
        raise RuntimeError("未知动作。")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as error:
        # D-Bus/parse errors may contain full monitor identities. Keep output local and minimal.
        print(f"操作结束：{error}" if type(error) is RuntimeError else
              "无法读取当前会话或 GNOME 显示配置；请核对依赖和接线。", file=sys.stderr)
        sys.exit(1)
