"""Install Droidloom window controls for the current GNOME desktop user."""
import ast
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

UUID = "tb-android-outer-shadow@tb710fu"
FAMILY = {
    UUID, "tb-android-window-defaults@tb710fu", "tb-android-focus-edges@tb710fu",
    "tb-android-windows@tb710fu", "tb-android-fullscreen@tb710fu",
    "tb-android-navigation@tb710fu", "tb-android-window-navigation@tb710fu",
    "tb-android-gestures@tb710fu", "tb-android-gestures-osk@tb710fu",
    "tb-android-gesture-feedback@tb710fu", "tb-android-navigation-controls@tb710fu",
}
PAYLOAD = ("metadata.json", "extension.js", "navigation.js", "stylesheet.css")
KEYS = ("disabled-extensions", "enabled-extensions")
SOURCE = Path(__file__).resolve().parent


def command(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=30).stdout


def settings(key):
    value = command("gsettings", "get", "org.gnome.shell", key).strip().removeprefix("@as ")
    result = ast.literal_eval(value)
    if not isinstance(result, list) or not all(isinstance(item, str) for item in result):
        raise ValueError("GNOME 扩展设置格式未知")
    return result


def set_settings(key, value):
    if value != settings(key):
        command("gsettings", "set", "org.gnome.shell", key, repr(value))
        if settings(key) != value:
            raise RuntimeError(f"GNOME 设置写入未生效：{key}")


def update_selection(old, key, restore=None):
    if restore is not None:
        updated = [item for item in old if item not in FAMILY]
        for index, item in restore:
            updated.insert(index, item)
        return updated
    if key == "disabled-extensions":
        return [item for item in old if item != UUID]
    updated = [item for item in old if item not in FAMILY]
    index = next((i for i, item in enumerate(old) if item in FAMILY), len(updated))
    updated.insert(index, UUID)
    return updated


def read_payload():
    result = {name: (SOURCE / name).read_bytes() for name in PAYLOAD}
    metadata = json.loads(result["metadata.json"])
    if metadata["uuid"] != UUID or "50" not in metadata["shell-version"]:
        raise ValueError("扩展附件标识或 GNOME 版本不匹配")
    return result


def matching(directory, payload):
    return (
        not directory.is_symlink() and directory.is_dir()
        and {entry.name for entry in directory.iterdir()} == set(payload)
        and all((directory / name).is_file() and not (directory / name).is_symlink()
                and (directory / name).read_bytes() == content for name, content in payload.items())
    )


def save(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(value, stream)
    Path(stream.name).replace(path)


def install_files(directory, payload):
    directory.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tb-droidloom-", dir=directory.parent))
    try:
        for name, content in payload.items():
            (stage / name).write_bytes(content)
        stage.rename(directory)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def read_state(path):
    before = json.loads(path.read_text())
    if before.get("format") != 1 or type(before.get("created")) is not bool:
        raise ValueError("无法识别本机回退记录")
    for key in KEYS:
        for index, item in before["settings"][key]:
            if type(index) is not int or index < 0 or item not in FAMILY:
                raise ValueError("无法识别本机扩展选择快照")
    return before


def rollback(state, directory, payload):
    before = read_state(state)
    if before["created"] and (directory.exists() or directory.is_symlink()) and not matching(directory, payload):
        raise ValueError("扩展目录已有其他修改，请核对本机快照后回退")
    for key in KEYS:
        set_settings(key, update_selection(settings(key), key, before["settings"][key]))
    if before["created"] and directory.exists():
        with tempfile.TemporaryDirectory(prefix=".tb-droidloom-remove-", dir=directory.parent) as stage:
            directory.rename(Path(stage) / "extension")
            state.unlink()
    else:
        state.unlink()


def apply(state, directory, payload):
    existing = directory.exists() or directory.is_symlink()
    if existing and not matching(directory, payload):
        raise ValueError("安装位置已有不同内容，请先保留原扩展")
    if state.exists():
        read_state(state)
    else:
        snapshot = {key: [(i, item) for i, item in enumerate(settings(key)) if item in FAMILY]
                    for key in KEYS}
        save(state, {"format": 1, "created": not existing, "settings": snapshot})
    try:
        if not existing:
            install_files(directory, payload)
        for key in KEYS:
            set_settings(key, update_selection(settings(key), key))
    except Exception:
        try:
            rollback(state, directory, payload)
        except Exception as error:
            print("自动回退中断，快照仍保留：", error, file=sys.stderr)
        raise


def paths():
    home = Path.home()
    share = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state")
    return share / "gnome-shell/extensions" / UUID, state / "tb710fu-droidloom-window-navigation-v13"


def preflight(require_droidloom):
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    model = Path("/sys/firmware/devicetree/base/model")
    if (os.uname().machine != "aarch64" or not model.is_file() or "TB710FU" not in model.read_text()
            or release.get("ID") != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "26.04"):
        raise ValueError("适用于 TB710FU Ubuntu 26.04 ARM64")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    display = os.environ.get("WAYLAND_DISPLAY")
    if os.geteuid() == 0 or not runtime or not display or not (Path(runtime) / display).is_socket():
        raise ValueError("请在已登录的 GNOME Wayland 桌面使用当前普通用户运行")
    if command("gnome-shell", "--version").split()[-1].split(".")[0] != "50":
        raise ValueError("扩展适用于 GNOME Shell 50")
    settings("enabled-extensions")
    if require_droidloom and command("gsettings", "get", "org.gnome.shell",
                                    "disable-user-extensions").strip() == "true":
        raise ValueError("请先在 GNOME 扩展设置中启用用户扩展")
    if require_droidloom and not Path("/usr/bin/droidloomctl").is_file():
        raise ValueError("请先应用目录中的 Droidloom 安装条目")


def main(action):
    if action not in {"check", "apply", "rollback"}:
        raise ValueError("操作须为 check、apply 或 rollback")
    preflight(action != "rollback")
    directory, state_dir = paths()
    payload = read_payload()
    state = state_dir / "before.json"
    if action == "check":
        print(f"桌面预检通过。扩展附件与已安装目录一致：{matching(directory, payload)}")
        print(f"本机回退记录：{'有' if state.is_file() else '无'}")
        return
    if state_dir.is_symlink() or state.is_symlink():
        raise ValueError("回退目录必须由当前用户直接管理")
    if action == "rollback" and not state.is_file():
        raise ValueError("没有本条目写入的回退记录")
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (state_dir / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "rollback":
            rollback(state, directory, payload)
            print("已恢复之前的安卓扩展选择。如有需要，请注销再登录。")
        else:
            apply(state, directory, payload)
            if UUID in command("gnome-extensions", "list", "--active").splitlines():
                print("安卓窗口和手势修复已生效。")
            else:
                print("扩展已安装并选中。请保存工作，注销并重新登录 GNOME 后验证。")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) == 2 else "check")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"安卓窗口修复未完成：{getattr(error, 'stderr', None) or error}", file=sys.stderr)
        sys.exit(1)
