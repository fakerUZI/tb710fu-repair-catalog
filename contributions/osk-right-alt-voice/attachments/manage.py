"""Install the reviewed user keyboard and WowType's locally downloaded speech model."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

UUID = "tb-osk-shortcuts@tb710fu"
SOURCE = Path(__file__).resolve().parent / "extension"
FILES = ("extension.js", "shift-modifier.js", "arrow-repeat.js", "candidate-row.js",
         "stylesheet.css", "metadata.json")
DATA = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share")
TARGET = DATA / "gnome-shell/extensions" / UUID
STATE = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local/state") / "tb710fu/osk-right-alt-voice"
CONFIG = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "fcitx5/conf/wowtype.conf"
MODEL = Path(os.getenv("WOWTYPE_VOICE_MODEL_DIR") or DATA / "wowtype/wtlocal")
WANTED = {"Voice": "True", "VoiceKey": "Alt_R"}
LISTS = ("enabled-extensions", "disabled-extensions")


class Conflict(ValueError):
    """Existing files need review before an install or a rollback."""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def replace(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            os.fchmod(out.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def model_ready():
    def populated(path):
        return path.is_file() and path.stat().st_size > 0
    return populated(MODEL / "dict.decoder.utf8.txt") and any(populated(p) for p in MODEL.glob("*.xnet"))


def ensure_model():
    if model_ready():
        return
    # Use the official downloader, checksum and unpacker. Isolate its settings
    # rewrite so unrelated preferences and translation credentials are untouched.
    print("正在通过 WowType 下载并校验约 103 MB 的离线语音包。", flush=True)
    with tempfile.TemporaryDirectory() as cfg:
        env = dict(os.environ, XDG_CONFIG_HOME=cfg, WOWTYPE_VOICE_MODEL_DIR=str(MODEL))
        subprocess.run(["wowtype-config", "--install-voice-model"], env=env, check=True)
    if not model_ready():
        raise ValueError("WowType 未准备好语音模型，请检查下载连接及可用空间后重试。")


def preferences():
    path = CONFIG.resolve()
    raw = path.read_bytes().decode() if path.exists() else ""
    lines = raw.splitlines(keepends=True)
    split = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    return lines[:split], lines[split:]


def pref_key(line):
    key, sep, _ = line.partition("=")
    return key.strip() if sep else None


def voice_lines():
    top, _ = preferences()
    return {key: [line for line in top if pref_key(line) == key] for key in WANTED}


def set_prefs(changes):
    if not changes:
        return
    top, groups = preferences()
    selected = [line for lines in changes.values() for line in lines]
    kept = [line for line in top if pref_key(line) not in changes]
    body = "".join(line if line.endswith(("\r", "\n")) else line + "\n" for line in selected)
    raw = (body + "".join(kept + groups)).encode()
    replace(CONFIG.resolve(), raw)


def effective(lines, default):
    return lines[-1].split("=", 1)[1].strip().strip('"') if lines else default


def membership(settings):
    return [UUID in settings.get_strv(key) for key in LISTS]


def select(settings, wanted):
    from gi.repository import Gio
    settings.delay()
    for key, want in zip(LISTS, wanted):
        values = settings.get_strv(key)
        if (UUID in values) != want:
            updated = values + [UUID] if want else [v for v in values if v != UUID]
            if not settings.set_strv(key, updated):
                settings.revert()
                raise ValueError("无法写入当前用户的 GNOME 扩展设置。")
    settings.apply()
    Gio.Settings.sync()
    if membership(settings) != list(wanted):
        raise ValueError("GNOME 扩展设置写后校验失败。")


def environment(settings):
    shell = subprocess.check_output(["gnome-shell", "--version"], text=True)
    if not re.search(r"\b50(?:\.|\s|$)", shell):
        raise ValueError("此键盘扩展适用于 GNOME Shell 50。")
    if settings.get_boolean("disable-user-extensions"):
        raise ValueError("请先在 GNOME 扩展中打开用户扩展总开关。")
    if not shutil.which("wowtype-config") or not shutil.which("fcitx5-remote"):
        raise ValueError("请先安装 Fcitx5 和带离线语音功能的 WowType。")


def baseline():
    # An existing user installation wins over the system copy, as in GNOME.
    bases = [TARGET] + [Path(d) / "gnome-shell/extensions" / UUID for d in
                         (os.getenv("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    for base in bases:
        if (base / "metadata.json").is_file():
            meta = json.loads((base / "metadata.json").read_bytes())
            if meta["uuid"] != UUID or meta["version"] > 22:
                raise ValueError("已有不同或更新的键盘扩展，请保留该版本并核对右 Alt 功能。")
            break
    if TARGET.is_symlink() or any((TARGET / name).is_symlink() for name in FILES):
        raise ValueError("用户键盘文件由符号链接管理，请先核对其管理方式。")


def snapshot(settings):
    # Publish the rollback record only after all original managed files are saved.
    with tempfile.TemporaryDirectory(dir=STATE) as folder:
        tmp = Path(folder) / "backup"
        tmp.mkdir()
        originals = {}
        for name in FILES:
            old = TARGET / name
            originals[name] = digest(old)
            if originals[name] is not None:
                shutil.copy2(old, tmp / name)
        doc = {"original": originals, "installed": {n: digest(SOURCE / n) for n in FILES},
               "membership": membership(settings), "prefs": voice_lines(), "user_dir": TARGET.exists()}
        (tmp / "record.json").write_text(json.dumps(doc))
        tmp.rename(STATE / "backup")


def reconcile(settings, rollback):
    backup = STATE / "backup"
    record = json.loads((backup / "record.json").read_bytes())
    for name in FILES:
        path = TARGET / name
        if path.is_symlink() or digest(path) not in (record["original"][name], record["installed"][name]):
            raise Conflict("键盘文件已被其他更新修改，请先核对：" + name)
        if record["original"][name] != digest(backup / name):
            raise Conflict("键盘回退副本校验失败：" + name)
        if not rollback and digest(SOURCE / name) != record["installed"][name]:
            raise Conflict("修复附件已变化，请先使用上一版条目回退。")
    for name in FILES:
        path = TARGET / name
        source = (backup if rollback else SOURCE) / name
        if digest(path) == digest(source):
            continue
        if source.exists():
            replace(path, source.read_bytes(), source.stat().st_mode & 0o777)
        else:
            path.unlink()
    expected = record["original" if rollback else "installed"]
    if any(digest(TARGET / name) != expected[name] for name in FILES):
        raise ValueError("键盘安装文件写后校验失败。")
    if rollback and not record["user_dir"] and TARGET.exists() and not any(TARGET.iterdir()):
        TARGET.rmdir()
    current = voice_lines()
    changes = {}
    for key, value in WANTED.items():
        if rollback:
            if current[key] == [key + "=" + value + "\n"] and record["prefs"][key] != current[key]:
                changes[key] = record["prefs"][key]
        elif effective(current[key], value) != value:
            changes[key] = [key + "=" + value + "\n"]
    set_prefs(changes)
    select(settings, record["membership"] if rollback else [True, False])
    if rollback:
        # A completed rollback is atomic with respect to retries.
        with tempfile.TemporaryDirectory(dir=STATE) as folder:
            backup.rename(Path(folder) / "restored")


def main(action, settings):
    if action not in ("check", "apply", "rollback") or os.geteuid() == 0:
        raise ValueError("请以桌面普通用户执行 check、apply 或 rollback。")
    if action == "check":
        environment(settings)
        baseline()
        print("用户键盘文件", "已匹配" if all(digest(TARGET / n) == digest(SOURCE / n) for n in FILES) else "待安装")
        print("离线模型", "已就绪" if model_ready() else "待下载")
        print("语音开关和热键", *(effective(v, WANTED[k]) for k, v in voice_lines().items()))
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "rollback":
            if (STATE / "backup").exists():
                if TARGET.is_symlink():
                    raise ValueError("用户键盘路径已变更，请先核对。")
                reconcile(settings, True)
            print("回退完成。共享语音模型保留。保存工作后重新登录。")
            return
        environment(settings)
        if not (STATE / "backup").exists():
            baseline()
        ensure_model()
        if not (STATE / "backup").exists():
            snapshot(settings)
        if TARGET.is_symlink():
            raise ValueError("用户键盘路径已变更，请先核对。")
        try:
            reconcile(settings, False)
        except Conflict:
            raise
        except Exception:
            try:
                reconcile(settings, True)
            except Exception as restore_error:
                print(f"已保留本机回退记录：{restore_error}", file=sys.stderr)
            raise
        print("右 Alt 语音键盘已安装。保存工作并重新登录，切至 WowType 中文输入后长按右 Alt。")


if __name__ == "__main__":
    try:
        from gi.repository import Gio
        main(sys.argv[1], Gio.Settings.new("org.gnome.shell"))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f"右 Alt 语音键盘：{error}")
