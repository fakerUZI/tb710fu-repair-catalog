#!/usr/bin/env python3
"""Check a live kShim installation and optionally prepare its Ubuntu EFI files."""
import argparse
import shutil
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import stat
import subprocess
import tempfile

ASSETS = Path(__file__).resolve().parent
FILES = ("boot/Image", "boot/sm8650-lenovo-tb710fu.dtb", "loader/entries/ubuntu.conf",
         "loader/loader.conf", "EFI/BOOT/BOOTAA64.EFI")
VERSION = "7.2.6-tb710fu-20261124"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(*args):
    return subprocess.check_output(args, text=True, env={**os.environ, "LC_ALL": "C"}).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def preflight(profile, action):
    require(os.geteuid() == 0, "run on the tablet with sudo")
    require(platform.machine() == "aarch64" and platform.release() == VERSION, "wrong running kernel")
    require(b"lenovo,tb710fu" in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"), "wrong board")
    require("androidboot.slot_suffix=_a" in Path("/proc/cmdline").read_text().split(), "unexpected slot")
    require(set(profile["files"]) == set(FILES), "incomplete EFI payload")
    for name, cells in profile["reservations"].items():
        node = Path("/proc/device-tree/reserved-memory") / name
        require((node / "no-map").read_bytes() == b"" and
                (node / "reg").read_bytes() == struct.pack(">4I", *cells), "handoff reservation changed: " + name)

    rows = []
    disks = [p for p in Path("/sys/class/block").glob("sd*") if not (p / "partition").exists()]
    require(len(disks) == 6, "unexpected UFS disk inventory")
    for disk in disks:
        dev = Path("/dev") / disk.name
        require("No problems found." in run("sgdisk", "-v", dev), "invalid GPT on " + disk.name)
        table = json.loads(run("sfdisk", "--json", dev))["partitiontable"]
        require(table["label"] == "gpt", "expected GPT on " + disk.name)
        rows.extend(table["partitions"])
    devices = {}
    for label in ("arch", "boot_a", *profile["firmware"]):
        matches = [p for p in rows if p.get("name") == label]
        node = (Path("/dev/disk/by-partlabel") / label).resolve(strict=True)
        require(len(matches) == 1 and str(node) == matches[0]["node"] and stat.S_ISBLK(node.stat().st_mode),
                "unexpected GPT entry: " + label)
        devices[label] = node
    require(run("findmnt", "-n", "-o", "SOURCE,FSTYPE,FSROOT", "/").split()
            == [str(devices["arch"]), "ext4", "/"], "expected Ubuntu at the root of arch")
    boot = devices["boot_a"]
    require(int(run("blockdev", "--getsize64", boot)) == 96 << 20, "boot_a 分区大小不同")
    current = digest(boot)
    require(current in (profile["previous_boot"], profile["uboot"]), "当前 boot_a 不在本条目适用范围内")
    sysboot = Path("/sys/class/block") / boot.name
    disk = Path("/dev") / run("lsblk", "-ndo", "PKNAME", boot)
    slot = run("sgdisk", "-i", (sysboot / "partition").read_text().strip(), disk)
    flags = int(re.search(r"(?m)^Attribute flags: ([0-9a-fA-F]{16})$", slot)[1], 16)
    require(flags & (1 << 50) and flags & (1 << 54) and not flags & (1 << 55),
            "GPT does not mark slot A active, successful and bootable")
    for label, expected in profile["firmware"].items():
        require(digest(devices[label]) == expected, "firmware changed: " + label)
    require((Path("/lib/modules") / VERSION).is_dir(), "missing Ubuntu kernel modules")
    for name in FILES:
        target = Path("/") / name
        require(target.resolve() == target, "symlink at " + name)
        parent = next(p for p in target.parents if p.exists())
        require(run("findmnt", "-no", "TARGET", "-T", target if target.exists() else parent) == "/",
                "U-Boot cannot read a separate mount: " + name)
        if action == "rollback":
            continue
        if current == profile["uboot"] or target.exists():
            require(target.is_file() and digest(target) == profile["files"][name], "existing file differs: " + name)

    return boot, current


def stage(profile, root):
    for name in FILES:
        target = Path("/") / name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tb-uefi-") as stream:
            stream.write((root / name).read_bytes())
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
            require(digest(Path(stream.name)) == profile["files"][name], "staged hash mismatch: " + name)
            os.link(stream.name, target)
    os.sync()
    for name in FILES:
        require(digest(Path("/") / name) == profile["files"][name], "installed hash mismatch: " + name)


def switch(boot, image, before, after):
    # Retain both images until the block-device readback is complete.
    previous = boot.read_bytes()
    candidate = image.read_bytes()
    require(len(previous) == len(candidate) and hashlib.sha256(previous).hexdigest() == before
            and hashlib.sha256(candidate).hexdigest() == after, "boot inputs changed before switch")
    devno = boot.stat().st_rdev
    def write_and_check(data, expected):
        fd = os.open(boot, os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as stream:
            st = os.fstat(stream.fileno())
            require(stat.S_ISBLK(st.st_mode) and st.st_rdev == devno, "boot device changed")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        run("blockdev", "--flushbufs", boot)
        require(digest(boot) == expected, "boot_a readback failed")
    try:
        write_and_check(candidate, after)
    except BaseException:
        write_and_check(previous, before)
        raise


BACKUP = Path("/var/backups/tb710fu-uboot-uefi/boot_a-kshim.img")
PKG_URL = ("https://github.com/deinthoedoka/tb710fu-update/releases/download/repo-aarch64/"
           "tb710fu-uboot-uefi-2026.11.25-1-aarch64.pkg.tar.zst")
DEB_URL = ("https://ports.ubuntu.com/ubuntu-ports/pool/universe/s/systemd/"
           "systemd-boot-efi_259.9-0ubuntu0.1_arm64.deb")


def download(url, path, expected):
    subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--proto", "=https",
                    "--proto-redir", "=https", "--retry", "2", "--max-time", "90", "--max-filesize", "2000000",
                    "-o", path, url], check=True)
    require(digest(path) == expected, "上游软件包校验失败")


def materialize(work, previous, profile):
    # The two offsets refer to fields inside the SHA-pinned kShim file.
    # UFS partition locations are discovered independently in preflight().
    root = work / "root"
    for name in FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
    require(digest(previous) == profile["previous_boot"], "kShim 来源不匹配")
    with previous.open("rb") as stream:
        stream.seek(4096 + 41943040)
        (root / "boot/Image").write_bytes(stream.read(46463488))
        stream.seek(4096 + 88406528)
        (work / "board.dtb").write_bytes(stream.read(142632))
    require(digest(work / "board.dtb") == "68916bf43ea88f7a12c535385b12d4d399ea6882cb3b2c30c691a33dac476433",
            "基础设备树不匹配")
    pkg = work / "upstream.pkg.tar.zst"
    download(PKG_URL, pkg, "ec2bb10d44fdf0adcf8ecfffa3ebde30385aee1ff1f769cbdb57ff867333d6c8")
    with (work / "boot.img").open("wb") as out:
        subprocess.run(["tar", "-xOf", pkg, "usr/share/tb710fu/boot/boot.img"], stdout=out, check=True)
    require(digest(work / "boot.img") == "d859faf0736fcddd777d41bd61138448bbbb3f9a5e23ecdc1c27baad9feba1c6",
            "上游 U-Boot 校验失败")
    for name in ("board.dtb", "boot.img"):
        subprocess.run(["git", "apply", "--binary", ASSETS / (name + ".patch")], cwd=work, check=True)
    (work / "board.dtb").replace(root / FILES[1])
    deb = work / "efi.deb"
    download(DEB_URL, deb, "3f4dc4946a1f501d58f873d12ce8f8a18afbe6e35cf7344079135d726ab1ddd9")
    subprocess.run(["dpkg-deb", "-x", deb, work / "debian"], check=True)
    shutil.copyfile(work / "debian/usr/lib/systemd/boot/efi/systemd-bootaa64.efi", root / FILES[-1])
    for name in ("loader/loader.conf", "loader/entries/ubuntu.conf"):
        shutil.copyfile(ASSETS / Path(name).name, root / name)
    for name in FILES:
        require(digest(root / name) == profile["files"][name], "生成文件校验失败：" + name)
    require(digest(work / "boot.img") == profile["uboot"], "生成的 U-Boot 与实测镜像不同")


def save_backup(boot, expected):
    BACKUP.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(BACKUP.resolve() == BACKUP and BACKUP.parent.stat().st_uid == 0
            and not BACKUP.parent.stat().st_mode & 0o022, "备份目录权限不正确")
    if not BACKUP.exists():
        with tempfile.NamedTemporaryFile(dir=BACKUP.parent) as tmp, boot.open("rb") as src:
            shutil.copyfileobj(src, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            require(digest(Path(tmp.name)) == expected, "原启动分区备份校验失败")
            os.link(tmp.name, BACKUP)
        os.sync()
    require(BACKUP.is_file() and digest(BACKUP) == expected, "已有回退镜像的哈希不同")


def migrate(action, profile):
    boot, current = preflight(profile, action)
    old, new = profile["previous_boot"], profile["uboot"]
    if action == "rollback":
        if current == old:
            print("boot_a 已是原 kShim。")
            return
        require(BACKUP.is_file() and digest(BACKUP) == old, "缺少本机安装前备份，无法自动回退")
        switch(boot, BACKUP, new, old)
        print("原 kShim 已回读校验，请保存工作后重启。")
        return
    if current == new:
        print("U-Boot 已通过校验。" if Path("/sys/firmware/efi").exists() else "U-Boot 已写入，等待手动重启。")
        return
    require(not Path("/sys/firmware/efi").exists(), "当前仍在 EFI 会话，请先重启进入 kShim")
    if action == "check":
        print("当前 kShim、Ubuntu 内核、固件及 GPT 符合迁移条件。")
        return
    with tempfile.TemporaryDirectory(prefix="tb710fu-uboot-") as folder:
        work = Path(folder)
        materialize(work, boot, profile)
        save_backup(boot, old)
        stage(profile, work / "root")
        switch(boot, work / "boot.img", old, new)
    print(f"U-Boot 写入并回读成功。原镜像在 {BACKUP}，请另存一份到电脑，保存工作后手动重启。")


def main():
    parser = argparse.ArgumentParser(description="TB710FU Ubuntu 启动器（单独安装）")
    parser.add_argument("action", choices=("check", "apply", "rollback"))
    args = parser.parse_args()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        migrate(args.action, json.loads((ASSETS / "profile.json").read_text()))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"U-Boot 迁移停止：{error}") from None
