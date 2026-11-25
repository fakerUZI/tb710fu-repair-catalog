"""TB710FU 1118 -> 1124. Keep kShim and the existing arch partition."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

OLD = "7.2.6-tb710fu-20261118"
NEW = "7.2.6-tb710fu-20261124"
ASSETS = Path(__file__).resolve().parent
BASE = Path("/var/backups/tb710fu-kernel-1118-to-1124")
MODULES = Path("/lib/modules")
ROOT = Path("/")
KEYBOARD = "kernel/drivers/input/keyboard/lenovo-pogo/lenovo_keyboard.ko"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def inspect(profile):
    require(Path("/etc/os-release").is_file() and platform.machine() == "aarch64"
            and b"lenovo,tb710fu" in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"),
            "机型不匹配")
    require(platform.release() in (OLD, NEW), "仅支持 1118 升级至 1124")
    require(os.geteuid() == 0 and "androidboot.slot_suffix=_a" in Path("/proc/cmdline").read_text().split(),
            "需要管理员权限、Ubuntu 和 A 槽")
    rows = []
    disks = [p for p in Path("/sys/class/block").glob("sd*") if not (p / "partition").exists()]
    require(len(disks) == 6, "UFS 磁盘布局不符")
    for p in disks:
        disk = Path("/dev") / p.name
        require("No problems found." in run("sgdisk", "-v", disk), "GPT 校验失败")
        table = json.loads(run("sfdisk", "--json", disk))["partitiontable"]
        require(table["label"] == "gpt", "GPT 类型不符")
        rows.extend(table["partitions"])
    devices = {}
    for name in ("arch", "boot_a", *profile["firmware"]):
        nodes = [Path(r["node"]) for r in rows if r.get("name") == name]
        node = (Path("/dev/disk/by-partlabel") / name).resolve(strict=True)
        require(nodes == [node] and stat.S_ISBLK(node.stat().st_mode), "分区标签不符：" + name)
        devices[name] = node
    require(run("findmnt", "-no", "SOURCE,FSTYPE,FSROOT", "/").split() ==
            [str(devices["arch"]), "ext4", "/"], "根分区应为 arch ext4")
    boot = devices["boot_a"]
    require(int(run("blockdev", "--getsize64", boot)) == profile["boot_size"], "boot_a 大小不符")
    disk = Path("/dev") / run("lsblk", "-ndo", "PKNAME", boot)
    number = (Path("/sys/class/block") / boot.name / "partition").read_text().strip()
    match = re.search(r"(?m)^Attribute flags: ([0-9A-Fa-f]{16})$", run("sgdisk", "-i", number, disk))
    require(match, "无法读取 A 槽状态")
    flags = int(match[1], 16)
    require(flags & (1 << 50) and flags & (1 << 54) and not flags & (1 << 55),
            "A 槽须已确认为成功启动。BOOTFIX 用户可先核对修复助手的 A 槽启动确认条目。")
    for name, expected in profile["firmware"].items():
        require(digest(devices[name]) == expected, "固件不匹配：" + name)
    return boot, digest(boot)


def patch(work, name):
    subprocess.run(["git", "apply", "--binary", ASSETS / name], cwd=work, check=True)


def rebuild_boot(work, boot, profile):
    image = work / "boot.img"
    shutil.copyfile(boot, image)
    if digest(image) == profile["legacy"]:
        patch(work, "legacy.patch")
    require(digest(image) == profile["boot_before"], "1118 启动镜像不匹配")
    patch(work, "boot.patch")
    require(digest(image) == profile["boot_after"], "1124 启动镜像校验失败")
    return image


def module_tree(work, profile):
    archive = work / "modules.tar.gz"
    with archive.open("xb") as output:
        for name in profile["parts"]:
            with (ASSETS / name).open("rb") as part:
                shutil.copyfileobj(part, output)
    require(digest(archive) == profile["archive"], "模块分片校验失败")
    root = work / "system"
    target = root / "lib/modules"
    target.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            parts = Path(member.name).parts
            require(parts and parts[0] == NEW and ".." not in parts and (member.isfile() or member.isdir()),
                    "模块包路径无效")
        tar.extractall(target, filter="data")
    require(KEYBOARD in (target / NEW / "modules.builtin").read_text().splitlines(), "内置键盘驱动缺失")
    subprocess.run(["depmod", "-b", root, NEW], check=True)
    return target / NEW


def companion_files(work, profile):
    """Stage versioned modules only for camera groups already on this tablet."""
    shared = []
    directories = {}
    for row in profile["addons"]:
        absolute = Path(row["target"])
        parent = ROOT / absolute.parent.relative_to("/")
        old_dir = parent.with_name(OLD)
        if not old_dir.is_dir():
            continue
        directory = work / "addons" / row["group"]
        directory.mkdir(parents=True, exist_ok=True)
        directories[parent] = directory
        candidate = directory / absolute.name
        if "asset" in row:
            shutil.copyfile(ASSETS / row["asset"], candidate)
        else:
            source = old_dir / absolute.name
            require(source.is_file() and not source.is_symlink(), "缺少 1118 配套文件：" + str(source))
            shutil.copyfile(source, candidate)
            if "patch" in row:
                require(digest(candidate) == row["source_sha"], "1118 模块版本不同：" + source.name)
                patch(directory, row["patch"])
        require(digest(candidate) == row["sha"], "1124 配套文件校验失败：" + candidate.name)
        candidate.chmod(row["mode"])
    for i, row in enumerate(profile["shared"]):
        target = ROOT / row["target"].lstrip("/")
        group = ROOT / "opt" / ("tb710fu-" + row["group"]) / NEW
        if group not in directories:
            unit = {"camera-test": "camera-test", "front-camera": "front-camera",
                    "flash": "flash-hardware", "macro": "macro-camera"}[row["group"]]
            enabled = subprocess.run(["systemctl", "is-enabled", "--quiet", f"tb710fu-{unit}.service"])
            require(enabled.returncode != 0, "已启用的服务缺少 1118 模块：" + unit)
            continue
        require(target.is_file() and not target.is_symlink() and digest(target) in row["accepted"],
                "现有配套脚本与已核对版本不同：" + str(target))
        candidate = ASSETS / row["asset"]
        if row["dispatch"]:
            runtime = directories[group] / "hardware.py"
            shutil.copyfile(candidate, runtime)
            first, rest = target.read_bytes().split(b"\n", 1)
            redirect = (f"import os, sys\nif os.uname().release == '{NEW}':\n"
                        f"    os.execv(sys.executable, [sys.executable, '-I', '{group}/hardware.py', *sys.argv[1:]])\n")
            candidate = work / f"dispatcher-{i}"
            candidate.write_bytes(first + b"\n" + redirect.encode() + rest)
            compile(candidate.read_bytes(), str(target), "exec")
        shared.append((target, candidate, row["mode"]))
    return directories, shared


def verify_modules(modules, directories):
    provided = {"lenovo_keyboard", "tb_pogo_overlay"}  # integrated into the new kernel and DTB
    for folder in directories.values():
        for module in folder.glob("*.ko"):
            require(run("modinfo", "-F", "vermagic", module).split()[0] == NEW, "外挂模块 ABI 不符")
            provided.add(run("modinfo", "-F", "name", module))
    loaded = {line.split()[0] for line in Path("/proc/modules").read_text().splitlines()}
    for name in sorted(loaded - provided):
        run("modprobe", "-d", modules.parents[2], "-S", NEW, "--show-depends", name)


def atomic(target, source, mode):
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
        temporary = Path(out.name)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(mode)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def tree_hashes(root):
    return {p.relative_to(root): digest(p) for p in root.rglob("*") if p.is_file()}


def install_tree(source, target):
    if os.path.lexists(target):
        require(target.is_dir() and not target.is_symlink() and tree_hashes(source) == tree_hashes(target),
                "已存在不同的 1124 文件：" + str(target))
        return
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".tb-kernel-") as tmp:
        stage = Path(tmp) / NEW
        shutil.copytree(source, stage)
        require(tree_hashes(source) == tree_hashes(stage), "安装后模块校验失败")
        stage.rename(target)


def backup(boot, before, shared):
    if not BASE.exists():
        with tempfile.TemporaryDirectory(dir=BASE.parent) as tmp:
            staging = Path(tmp) / "backup"
            staging.mkdir(mode=0o700)
            shutil.copyfile(boot, staging / "boot_a.img")
            require(digest(staging / "boot_a.img") == before, "启动分区备份校验失败")
            rows = []
            for index, (target, candidate, _) in enumerate(shared):
                saved = staging / str(index)
                shutil.copyfile(target, saved)
                require(digest(saved) == digest(target), "脚本备份校验失败")
                rows.append(dict(target=str(target), before=digest(saved), after=digest(candidate),
                                 mode=stat.S_IMODE(target.stat().st_mode)))
            (staging / "state.json").write_text(json.dumps(dict(boot=before, files=rows)))
            staging.rename(BASE)
        os.sync()
    return checked_backup(before)


def checked_backup(before):
    state = json.loads((BASE / "state.json").read_bytes())
    require(state["boot"] == before and digest(BASE / "boot_a.img") == before, "本机备份不匹配")
    for index, row in enumerate(state["files"]):
        require(digest(BASE / str(index)) == row["before"], "脚本备份损坏")
        target = Path(row["target"])
        require(not target.is_symlink() and digest(target) in (row["before"], row["after"]),
                "配套文件有后续改动：" + str(target))
    return state


def restore_shared(state):
    for index, row in enumerate(state["files"]):
        target = Path(row["target"])
        current = digest(target)
        require(current in (row["before"], row["after"]), "拒绝覆盖已改动的配套文件：" + str(target))
        if current != row["before"]:
            atomic(target, BASE / str(index), row["mode"])
    os.sync()


def write_boot(boot, image, expected):
    rdev = boot.stat().st_rdev
    fd = os.open(boot, os.O_WRONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "wb") as target, image.open("rb") as src:
        st = os.fstat(target.fileno())
        require(stat.S_ISBLK(st.st_mode) and st.st_rdev == rdev and digest(image) == expected,
                "写入目标或镜像变化")
        shutil.copyfileobj(src, target, 4 << 20)
        target.flush()
        os.fsync(target.fileno())
    run("blockdev", "--flushbufs", boot)
    require(digest(boot) == expected, "boot_a 完整回读失败")


def install(work, boot, before, profile):
    if BASE.exists():
        restore_shared(checked_backup(before))
    image = rebuild_boot(work, boot, profile)
    modules = module_tree(work, profile)
    directories, shared = companion_files(work, profile)
    verify_modules(modules, directories)
    directories[MODULES / NEW] = modules
    state = backup(boot, before, shared)
    require([(str(t), digest(t), digest(c)) for t, c, _ in shared] ==
            [(r["target"], r["before"], r["after"]) for r in state["files"]], "配套文件已变化")
    for target, source in directories.items():
        install_tree(source, target)
    writing = False
    try:
        for (target, candidate, mode), row in zip(shared, state["files"]):
            require(digest(target) == row["before"], "配套脚本在准备期间变化")
            atomic(target, candidate, mode)
            require(digest(target) == row["after"], "配套脚本写入校验失败")
        run("systemctl", "daemon-reload")
        os.sync()
        require(digest(boot) == before, "boot_a 在准备期间变化")
        writing = True
        write_boot(boot, image, profile["boot_after"])
    except BaseException:
        if writing:
            write_boot(boot, BASE / "boot_a.img", before)
        restore_shared(state)
        run("systemctl", "daemon-reload")
        raise
    print(f"1124 已写入并完整回读。原镜像保存在 {BASE}/boot_a.img，请另存到电脑后手动重启。")


def execute(action, profile):
    boot, current = inspect(profile)
    running = platform.release()
    old = (profile["boot_before"], profile["legacy"])
    if action == "rollback":
        require(current != profile["uefi"], "请先通过 U-Boot 条目回退到 1124 kShim，再回退内核")
        require(current in (*old, profile["boot_after"]), "boot_a 已变化")
        state = json.loads((BASE / "state.json").read_bytes())
        require(state["boot"] in old, "原镜像不在支持范围内")
        state = checked_backup(state["boot"])
        if current != state["boot"]:
            require(current == profile["boot_after"], "现有启动镜像与备份不匹配")
            # If restoring 1118 fails, recover the verified 1124 image before returning.
            with tempfile.TemporaryDirectory() as tmp:
                saved = Path(tmp) / "1124.img"
                shutil.copyfile(boot, saved)
                require(digest(saved) == current, "1124 回退保护副本校验失败")
                try:
                    write_boot(boot, BASE / "boot_a.img", state["boot"])
                except BaseException:
                    write_boot(boot, saved, current)
                    raise
        restore_shared(state)
        run("systemctl", "daemon-reload")
        print("已恢复本机 1118 启动镜像和原配套脚本。请手动重启。")
        return
    if current in (profile["boot_after"], profile["uefi"]):
        require((MODULES / NEW / "modules.dep").is_file(), "缺少 1124 模块目录")
        if current == profile["uefi"]:
            require(running == NEW and Path("/sys/firmware/efi").is_dir(), "当前启动状态不匹配")
        print("已运行 1124。" if running == NEW else "1124 已写入，请先重启再运行 U-Boot 条目。")
        return
    require(running == OLD and current in old and not Path("/sys/firmware/efi").exists(),
            "需要匹配的 1118 kShim；如刚回退，请先重启")
    require(hashlib.sha256(gzip.decompress(Path("/proc/config.gz").read_bytes())).hexdigest()
            == profile["config_before"], "1118 内核配置不同")
    if action == "check":
        print("1118 启动镜像、固件与 GPT 检查通过。安装时将校验完整模块包和本机配套文件。")
        return
    with tempfile.TemporaryDirectory(prefix="tb-kernel-1124-") as tmp:
        install(Path(tmp), boot, current, profile)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "apply", "rollback"))
    args = parser.parse_args()
    lock = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        execute(args.action, json.loads((ASSETS / "profile.json").read_bytes()))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"1124 内核升级停止：{exc}") from None
    finally:
        os.close(lock)
