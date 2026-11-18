"""Configure the tested TB710FU zram and free-memory watermarks for the next boot."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
BACKUP = Path("/var/backups/tb710fu-droidloom-window-memory")
GENERATOR = "systemd-zram-generator"
ZRAM = Path("/etc/systemd/zram-generator.conf.d/60-tb710fu-droidloom.conf")
WATERMARK = Path("/etc/sysctl.d/60-tb710fu-droidloom-memory.conf")
CONFIGS = {"zram": ZRAM, "watermark": WATERMARK}
SEARCH = (Path("/etc/systemd"), Path("/run/systemd"), Path("/usr/local/lib/systemd"),
          Path("/usr/lib/systemd"))


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True, timeout=180)


def installed(name):
    result = run("dpkg-query", "-W", "-f", "${db:Status-Status}", name, check=False)
    return result.returncode == 0 and result.stdout == "installed"


def exists(path):
    return path.exists() or path.is_symlink()


def matches(path, content):
    return not path.is_symlink() and path.is_file() and path.read_bytes() == content


def payload():
    return {key: (SOURCE / target.name).read_bytes() for key, target in CONFIGS.items()}


def check_configs(contents):
    for key, path in CONFIGS.items():
        if exists(path) and not matches(path, contents[key]):
            raise ValueError(f"本机配置已有其他内容，请先检查：{path}")


def read_snapshot(path):
    before = json.loads(path.read_text())
    if (before.get("format") != 1 or not isinstance(before.get("created"), list)
            or not set(before["created"]).issubset(CONFIGS)
            or type(before.get("generator_installed")) is not bool):
        raise ValueError("无法识别本机回退记录")
    return before


def create(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent) as stream:
        stream.write(content)
        stream.flush()
        os.chmod(stream.name, mode)
        # Refuse to replace files that appeared since preflight.
        os.link(stream.name, path)


def check_provider():
    if installed("zram-tools"):
        raise ValueError("系统已安装 zram-tools，请先确认现有压缩交换管理方式")
    for base in SEARCH:
        files = list((base / "zram-generator.conf.d").glob("*.conf"))
        if base != Path("/usr/lib/systemd"):
            files.append(base / "zram-generator.conf")
        for file in files:
            if file != ZRAM and file.is_file() and re.search(
                    r"^\s*\[zram0\]\s*(?:[#;].*)?$", file.read_text(), re.MULTILINE):
                raise ValueError(f"已有 zram0 本地配置：{file}")


def preflight(action, contents):
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    model = Path("/sys/firmware/devicetree/base/model")
    if (os.uname().machine != "aarch64" or not model.is_file() or "TB710FU" not in model.read_text()
            or release.get("ID") != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "26.04"):
        raise ValueError("适用于 TB710FU Ubuntu 26.04 ARM64")
    check_configs(contents)
    if action != "rollback":
        if not Path("/usr/bin/droidloomctl").is_file():
            raise ValueError("请先应用目录中的 Droidloom 安装条目")
        if not Path("/sys/module/zram").exists() and run(
                "/usr/sbin/modinfo", "-F", "filename", "zram", check=False).returncode:
            raise ValueError("当前内核没有 zram 支持")
        check_provider()
    if action != "check" and os.geteuid() != 0:
        raise ValueError("应用及回退需要管理员权限")


def rollback(state, contents):
    before = read_snapshot(state)
    check_configs(contents)
    if not before["generator_installed"] and installed(GENERATOR):
        # dpkg removes only this package and honors existing dependencies.
        run("dpkg", "--remove", GENERATOR)
    for key in before["created"]:
        CONFIGS[key].unlink(missing_ok=True)
    state.unlink()


def apply(state, contents):
    check_configs(contents)
    if state.exists():
        read_snapshot(state)
    else:
        before = {"format": 1, "created": [key for key, path in CONFIGS.items() if not exists(path)],
                  "generator_installed": installed(GENERATOR)}
        create(state, (json.dumps(before) + "\n").encode(), 0o600)
    try:
        for key, path in CONFIGS.items():
            if not exists(path):
                create(path, contents[key], 0o644)
        if not installed(GENERATOR):
            run("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y",
                "--no-install-recommends", "--no-upgrade", "install", GENERATOR)
    except Exception:
        try:
            rollback(state, contents)
        except Exception as error:
            print("自动回退中断，快照仍保留：", error, file=sys.stderr)
        raise


def main(action):
    if action not in {"check", "apply", "rollback"}:
        raise ValueError("操作须为 check、apply 或 rollback")
    contents = payload()
    preflight(action, contents)
    if action == "check":
        print("zram 预检通过。当前交换设备和优先级如下。")
        print(Path("/proc/swaps").read_text(), end="")
        return
    state = BACKUP / "before.json"
    if BACKUP.is_symlink() or state.is_symlink():
        raise ValueError("回退目录必须由管理员直接管理")
    if action == "rollback" and not state.is_file():
        raise ValueError("没有本条目写入的回退记录")
    BACKUP.mkdir(mode=0o700, exist_ok=True)
    with (BACKUP / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "rollback":
            rollback(state, contents)
            print("已恢复启动配置。请保存工作，在方便时重新启动。")
        else:
            apply(state, contents)
            print("已保存压缩交换和内存回收配置。请保存工作，在方便时重新启动。")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) == 2 else "check")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"安卓内存修复未完成：{getattr(error, 'stderr', None) or error}", file=sys.stderr)
        sys.exit(1)
