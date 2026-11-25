"""Upgrade the TB710FU Ubuntu installation from the official ARM64 packages."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

# The assistant stages verified attachments together and runs Python in isolated mode.
sys.path.append(str(Path(__file__).resolve().parent))
import droidloom_updates as upstream

UPSTREAM = "https://github.com/denialwm/droidloom/releases/download/packages/"
# Reuse old cached downloads, locks and rollback snapshots across script updates.
CACHE = Path("/var/cache/tb710fu-droidloom-r49")
BACKUP = Path("/var/backups/tb710fu-droidloom-r49")
ROOTS = (Path("/usr/lib/droidloom"), Path("/usr/share/droidloom"))
VERSION = ROOTS[1] / "package.json"
CTL = Path("/usr/bin/droidloomctl")
HELPER = ROOTS[0] / "droidloom-package-helper"
SPEC = Path("/etc/droidloom/cell.json")
CONFIG = (SPEC.parent, Path("/etc/polkit-1/rules.d/49-droidloom.rules"))
PACKAGE_PATHS = (
    "usr/bin/droidloom*",
    "usr/lib/systemd/system/droidloom*",
    "usr/lib/systemd/user/droidloom*",
    "usr/share/licenses/droidloom-*",
    "usr/share/doc/droidloom",
    "usr/share/polkit-1/actions/org.droidloom.setup.policy",
)


def run(*args, capture=False, check=True):
    options = {"text": True, "check": check}
    if capture:
        options["stdout"] = subprocess.PIPE
        options["stderr"] = subprocess.STDOUT
    return subprocess.run(args, **options)


def present(path):
    return path.exists() or path.is_symlink()


def json_file(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"无效的 JSON 配置 {path}")
    return value


def host_check():
    info = Path("/etc/os-release").read_text().splitlines()
    model = Path("/proc/device-tree/model").read_bytes()
    if os.uname().machine != "aarch64" or b"TB710FU" not in model or "ID=ubuntu" not in info:
        raise ValueError("该升级仅适用于 TB710FU 的 Ubuntu ARM64。")
    if 'VERSION_ID="26.04"' not in info:
        raise ValueError("该升级包适用于 Ubuntu 26.04。")


def desktop():
    uid = int(os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or "0")
    if uid < 1000:
        raise ValueError("请从当前桌面账号通过修复助手或 sudo 执行。")
    account = pwd.getpwuid(uid)
    runtime = Path(f"/run/user/{uid}")
    user = (
        "runuser", "-u", account.pw_name, "--", "env",
        f"HOME={account.pw_dir}", f"XDG_RUNTIME_DIR={runtime}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime / 'bus'}",
        "/usr/bin/systemctl", "--user",
    )
    env = run(*user, "show-environment", capture=True).stdout
    wayland = next((line.split("=", 1)[1] for line in env.splitlines()
                    if line.startswith("WAYLAND_DISPLAY=")), "")
    if not wayland or not (runtime / wayland).is_socket():
        raise ValueError("需在当前账号的 Wayland 桌面会话运行。")
    return uid, user


def checked_storage(uid):
    for path in (SPEC.parent, SPEC):
        info = path.lstat()
        if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"当前配置权限异常 {path}")
    spec = json_file(SPEC)
    if spec.get("host_uid") != uid:
        raise ValueError("现有 Droidloom 配置属于其他桌面账号。")
    if spec.get("gapps_dir") or present(ROOTS[0] / "gapps"):
        raise ValueError("检测到 Google 应用扩展，需要先核对与目标版本匹配的扩展。")
    storage = Path(spec["data_dir"])
    if not storage.is_absolute() or not storage.resolve().is_relative_to("/var/lib/droidloom"):
        raise ValueError("现有 Android 数据目录需要人工核对。")
    if not (storage / "data").is_dir() or not (storage / "metadata").is_dir():
        raise ValueError("该升级需要已迁移到宿主目录的 Android 数据。")
    return str(storage)


def valid_archive(path, size, expected):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected


def download(name, size, expected):
    target = CACHE / name
    partial = target.with_suffix(".part")
    if valid_archive(target, size, expected):
        return target
    if partial.exists() and partial.stat().st_size >= size:
        if valid_archive(partial, size, expected):
            partial.replace(target)
            return target
        partial.unlink()
    command = (
        "/usr/bin/curl", "-fSL", "--proto", "=https", "--proto-redir", "=https",
        "--connect-timeout", "20", "--retry", "3", "--output", partial,
    )
    result = run(*command, "--continue-at", "-", UPSTREAM + name, check=False)
    if result.returncode == 33:
        run(*command, UPSTREAM + name)
    else:
        result.check_returncode()
    if not valid_archive(partial, size, expected):
        partial.unlink()
        raise ValueError(f"官方软件包大小或 SHA-256 校验失败 {name}")
    partial.replace(target)
    return target


def stage_packages(stage, target, packages):
    for name, size, expected in packages:
        archive = download(name, size, expected)
        run("tar", "--use-compress-program=zstd", "--no-same-owner", "-xf", archive, "-C", stage)
    if upstream.installed_version(stage / VERSION.relative_to("/")) != target:
        raise ValueError("暂存的 Droidloom 发行版与预期不符。")
    images = stage / "usr/lib/droidloom/images/images"
    for name in ("system.img", "system_ext.img", "product.img", "vendor.raw.img"):
        if not (images / name).is_file():
            raise ValueError(f"安装包缺少 Android 镜像 {name}")
    paths = list(ROOTS)
    for pattern in PACKAGE_PATHS:
        paths.extend(Path("/") / file.relative_to(stage) for file in sorted(stage.glob(pattern)))
    required = (CTL, HELPER, Path("/usr/bin/droidloom-wayland"),
                Path("/usr/lib/systemd/user/droidloom.service"), Path("/usr/lib/systemd/system/droidloomd.service"))
    for path in required:
        if not (stage / path.relative_to("/")).is_file():
            raise ValueError(f"新版软件包布局已变化，缺少 {path}")
    # Only the known host locations are copied. Arch hook files are replaced by
    # the explicit stop / after-install steps on Ubuntu.
    managed = {stage / path.relative_to("/") for path in paths}
    hooks = stage / "usr/share/libalpm/hooks"
    metadata = {stage / name for name in (".MTREE", ".BUILDINFO", ".PKGINFO")}
    pending = list(stage.iterdir())
    while pending:
        entry = pending.pop()
        if entry in managed or entry in metadata or (entry.parent == hooks and entry.match("*.hook")):
            continue
        if entry.is_dir() and not entry.is_symlink():
            pending.extend(entry.iterdir())
        else:
            raise ValueError(f"新版软件包包含未适配的文件 {entry.relative_to(stage)}")
    for path in [item for item in paths if item.parent == CTL.parent] + [HELPER]:
        result = run("ldd", stage / path.relative_to("/"), capture=True, check=False)
        if result.returncode or "not found" in result.stdout:
            raise ValueError(f"运行库检查失败 {path.name}\n{result.stdout.strip()}")
    return paths


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    run("cp", "-a", "--", source, target)


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def snapshot(paths, uid, release, running, target):
    state = {"uid": uid, "release": release, "running": running, "target": target, "paths": [str(p) for p in paths]}
    with tempfile.TemporaryDirectory(prefix=".droidloom-r49-", dir=BACKUP.parent) as pending:
        staged = Path(pending) / "snapshot"
        staged.mkdir(mode=0o700)
        for path in paths:
            if present(path):
                copy(path, staged / "files" / path.relative_to("/"))
        (staged / "state.json").write_text(json.dumps(state))
        previous = None
        if present(BACKUP):
            previous = BACKUP.with_name(f"{BACKUP.name}-{time.time_ns()}")
            BACKUP.rename(previous)
        try:
            staged.rename(BACKUP)
        except OSError:
            if previous is not None:
                previous.rename(BACKUP)
            raise
    return state


def stop(user):
    run(*user, "stop", "droidloom.service")
    run("systemctl", "stop", "droidloomd.service")


def reload_units(user):
    run("systemctl", "daemon-reload")
    run(*user, "daemon-reload")


def restore(user, state):
    stop(user)
    (BACKUP / "completed").unlink(missing_ok=True)
    for name in reversed(state["paths"]):
        path = Path(name)
        original = BACKUP / "files" / path.relative_to("/")
        remove(path)
        if present(original):
            copy(original, path)
    reload_units(user)
    if state["running"]:
        run(*user, "start", "droidloom.service")
    (BACKUP / "state.json").rename(BACKUP / "restored.json")


def previous_snapshot(uid):
    if not present(BACKUP):
        return
    if BACKUP.is_symlink() or BACKUP.stat().st_uid != 0:
        raise ValueError(f"快照目录权限异常 {BACKUP}")
    marker = BACKUP / "restored.json"
    if (BACKUP / "completed").is_file():
        marker = BACKUP / "state.json"
    if not marker.is_file() or json_file(marker).get("uid") != uid:
        raise ValueError(f"有未完成的快照或属于其他账号，请检查 {BACKUP}")


def apply(uid, user, release, target, packages):
    previous_snapshot(uid)
    if run("dpkg-query", "-S", CTL, capture=True, check=False).returncode == 0:
        raise ValueError("当前安装由 dpkg 管理，请沿用原软件包渠道。")
    storage = checked_storage(uid)
    for name in ("curl", "tar", "zstd", "ldd"):
        if shutil.which(name) is None:
            raise ValueError(f"缺少系统工具 {name}")
    if shutil.disk_usage("/var").free < 10 * 1024**3:
        raise ValueError("请先为下载、解包和回退快照预留 10 GiB。")
    CACHE.mkdir(mode=0o700, exist_ok=True)
    info = CACHE.lstat()
    if CACHE.is_symlink() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("已有下载缓存的属主或权限异常。")
    with tempfile.TemporaryDirectory(prefix="tb710fu-droidloom-r49-", dir="/var/tmp") as work:
        stage = Path(work)
        paths = stage_packages(stage, target, packages)
        running = run(*user, "is-active", "--quiet", "droidloom.service", check=False).returncode == 0
        state = None
        try:
            stop(user)
            state = snapshot([*paths, *CONFIG], uid, release, running, target)
            for path in paths:
                remove(path)
                copy(stage / path.relative_to("/"), path)
            run(HELPER, "after-install")
            if checked_storage(uid) != storage:
                raise ValueError("升级过程中 Android 数据目录发生变化。")
            reload_units(user)
            (BACKUP / "completed").touch()
        except Exception:
            if state is not None:
                try:
                    restore(user, state)
                except Exception as error:
                    print(f"自动回退中断。快照保存在 {BACKUP}。{error}", file=sys.stderr)
            elif running:
                run(*user, "start", "droidloom.service", check=False)
            raise
    print(f"已升级到 Droidloom {target}。用桌面账号运行 droidloomctl start 启动。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "apply", "rollback"))
    action = parser.parse_args().action
    host_check()
    if action == "check":
        current = upstream.installed_version(VERSION)
        target, _ = upstream.latest()
        print(f"本机 Droidloom {current}，官方 ARM64 仓库 {target}。")
        return
    if os.geteuid() != 0:
        raise ValueError("升级与回退需要管理员权限。")
    with open("/run/lock/tb710fu-droidloom-r49.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "apply":
            if (BACKUP / "state.json").exists() and not (BACKUP / "completed").exists():
                raise ValueError("上次升级中断，请先使用 rollback 恢复快照。")
            release = upstream.installed_version(VERSION)
            target, packages = upstream.latest()
            if upstream.version_key(release) >= upstream.version_key(target):
                print(f"无需升级。本机 {release}，官方仓库 {target}。")
                return
            uid, user = desktop()
            apply(uid, user, release, target, packages)
            return
        uid, user = desktop()
        if not (BACKUP / "state.json").is_file():
            raise ValueError("本机没有此脚本的回退快照。")
        state = json_file(BACKUP / "state.json")
        if state.get("uid") != uid or BACKUP.is_symlink() or BACKUP.stat().st_uid != 0:
            raise ValueError("不能使用当前快照回退。")
        if (BACKUP / "completed").exists() and upstream.installed_version(VERSION) != state.get("target", "0.1.0-49"):
            raise ValueError("Droidloom 版本已变化，请人工核对后再回退。")
        restore(user, state)
        print("Droidloom 软件和主机配置已回退。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
