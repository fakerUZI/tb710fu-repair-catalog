"""Locate the two boot targets on the UFS controller hosting the running root."""
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess

SYS_BLOCK = Path("/sys/class/block")
DEV = Path("/dev")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def locate_targets():
    source, fs, fsroot = run("findmnt", "-no", "SOURCE,FSTYPE,FSROOT", "/").split()
    root = Path(source).resolve(strict=True)
    if fs != "ext4" or fsroot != "/" or not stat.S_ISBLK(root.stat().st_mode):
        raise ValueError("根目录须挂载自内置 arch ext4 分区")
    sysroot = (SYS_BLOCK / root.name).resolve(strict=True)
    for host in sysroot.parents:
        driver = host / "scsi_host" / host.name / "proc_name"
        if driver.is_file() and driver.read_text().strip() == "ufshcd":
            break
    else:
        raise ValueError("当前根设备不在内置 UFS 上")

    targets = {}
    firmware = []
    for entry in SYS_BLOCK.iterdir():
        path = entry.resolve()
        if host not in path.parents or not (path / "partition").is_file():
            continue
        info = dict(line.split("=", 1) for line in (path / "uevent").read_text().splitlines() if "=" in line)
        label = info.get("PARTNAME")
        if label == "abl_a":
            firmware.append(DEV / path.name)
        if label not in ("arch", "boot_a"):
            continue
        if label in targets:
            raise ValueError("内置 UFS 上分区重名：" + label)
        targets[label] = path
    if targets.get("arch") != sysroot or "boot_a" not in targets:
        raise ValueError("找不到与当前根目录匹配的内置 arch 和 boot_a")

    if len(firmware) == 1:
        try:
            with firmware[0].open("rb") as stream:
                fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
            print("当前 A 槽 ABL 固件 SHA-256（仅供诊断）：" + fingerprint)
        except OSError:
            print("当前 ABL 固件不可读，继续核对启动目标。")
    tables = {}
    for label, part in targets.items():
        disk = DEV / part.parent.name
        node = DEV / part.name
        if not stat.S_ISBLK(node.stat().st_mode):
            raise ValueError("目标不是块设备：" + label)
        if disk not in tables:
            tables[disk] = json.loads(run("sfdisk", "--json", disk))["partitiontable"]
        table = tables[disk]
        rows = [row for row in table["partitions"] if row.get("name") == label]
        if (table["label"] != "gpt" or table["unit"] != "sectors" or len(rows) != 1
                or rows[0]["node"] != str(node)):
            raise ValueError("当前 GPT 与目标分区不一致：" + label)
        row = rows[0]
        start, end = row["start"], row["start"] + row["size"]
        if any(other is not row and start < other["start"] + other["size"] and other["start"] < end
               for other in table["partitions"]):
            raise ValueError("目标分区与另一分区重叠：" + label)
        sector = int(table["sectorsize"])
        if any(int(row[field]) * sector != int((part / field).read_text()) * 512
               for field in ("start", "size")):
            raise ValueError("GPT 与内核识别的分区范围不同：" + label)
    boot = targets["boot_a"]
    return DEV / boot.name, DEV / boot.parent.name, int((boot / "partition").read_text())


def checked_boot(size):
    boot, disk, number = locate_targets()
    if int(run("blockdev", "--getsize64", boot)) != size:
        raise ValueError("boot_a 分区大小与启动镜像不同")
    info = run("sgdisk", "-i", str(number), disk)
    flags = re.search(r"(?m)^Attribute flags: ([0-9A-Fa-f]{16})$", info)
    if not flags:
        raise ValueError("无法读取 boot_a 的启动属性")
    value = int(flags[1], 16)
    if not (value & (1 << 50) and value & (1 << 54)) or value & (1 << 55):
        raise ValueError("A 槽须已激活、可启动并已标记成功；请核对 A 槽启动确认条目")
    return boot
