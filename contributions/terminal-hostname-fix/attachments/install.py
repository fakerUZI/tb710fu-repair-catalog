"""Append the running hostname to /etc/hosts so sudo stops reporting unresolvable hosts."""
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
from pathlib import Path

HOSTS = Path("/etc/hosts")
BACKUP = Path("/var/backups/tb710fu-terminal-hostname-fix")
LOOPBACK = "127.0.1.1"


def hostname():
    name = socket.gethostname().strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", name):
        raise ValueError(f"主机名不适合写入 /etc/hosts：{name!r}")
    return name


def entry_line(name):
    return f"{LOOPBACK}\t{name}\n".encode()


def read_hosts():
    if HOSTS.is_symlink() or not HOSTS.is_file():
        raise ValueError("/etc/hosts 不是常规文件，请先人工检查")
    return HOSTS.read_bytes()


def find_hostname_tokens(current, name):
    tokens = []
    for number, line in enumerate(current.decode("utf-8").splitlines(), 1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        fields = stripped.split()
        if any(alias.casefold() == name.casefold() for alias in fields[1:]):
            tokens.append((number, fields[0]))
    return tokens


def needs_repair(current, name):
    tokens = find_hostname_tokens(current, name)
    for number, address in tokens:
        if address != LOOPBACK:
            raise ValueError(f"/etc/hosts 第 {number} 行已把 {name} 指向 {address}，请先人工确认")
    return not tokens


def replace_file(path, content, *, expected_hosts=None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            if expected_hosts is not None:
                original = HOSTS.stat(follow_symlinks=False)
                os.fchown(stream.fileno(), original.st_uid, original.st_gid)
                shutil.copystat(HOSTS, temporary)
                os.utime(temporary, None)
            os.fsync(stream.fileno())
        if expected_hosts is not None and read_hosts() != expected_hosts:
            raise ValueError("/etc/hosts 在预检后被修改，请重新执行")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("回退记录必须是普通文件")
    before = json.loads(path.read_text())
    if not isinstance(before, dict) or before.get("format") != 2:
        raise ValueError("无法识别本机回退记录")
    after_hash = before.get("applied_sha256")
    content = before.get("content")
    if not isinstance(content, str) or not isinstance(after_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", after_hash):
        raise ValueError("本机回退记录格式异常")
    original = base64.b64decode(content, validate=True)
    if hashlib.sha256(original).hexdigest() != before.get("sha256"):
        raise ValueError("本机回退记录校验失败")
    return original, after_hash


def apply(name):
    current = read_hosts()
    if not needs_repair(current, name):
        print(f"/etc/hosts 已包含 {LOOPBACK} 到 {name} 的映射，无需修改。")
        return
    separator = b"\n" if current and not current.endswith(b"\n") else b""
    updated = current + separator + entry_line(name)
    after_hash = hashlib.sha256(updated).hexdigest()
    state_path = BACKUP / "before.json"
    if state_path.exists() or state_path.is_symlink():
        if load_state(state_path) != (current, after_hash):
            raise ValueError("已有其他版本的回退记录，请先核对")
    else:
        before = {"format": 2, "sha256": hashlib.sha256(current).hexdigest(), "applied_sha256": after_hash,
                  "content": base64.b64encode(current).decode("ascii")}
        replace_file(state_path, (json.dumps(before) + "\n").encode())
    replace_file(HOSTS, updated, expected_hosts=current)
    print(f"已向 /etc/hosts 追加 {LOOPBACK} {name}。请重新运行 sudo 验证。")


def rollback():
    state_path = BACKUP / "before.json"
    if not state_path.exists() and not state_path.is_symlink():
        raise ValueError("没有本条目写入的回退记录")
    original, after_hash = load_state(state_path)
    current = read_hosts()
    if current != original:
        if hashlib.sha256(current).hexdigest() != after_hash:
            raise ValueError("/etc/hosts 在应用后另有改动，已保留当前文件及回退记录")
        replace_file(HOSTS, original, expected_hosts=current)
    state_path.unlink()
    print("已恢复原 /etc/hosts。")


def main(action):
    if action not in {"check", "apply", "rollback"}:
        raise ValueError("操作须为 check、apply 或 rollback")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    model = Path("/sys/firmware/devicetree/base/model")
    if (os.uname().machine != "aarch64" or not model.is_file() or b"TB710FU" not in model.read_bytes()
            or release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "26.04"):
        raise ValueError("适用于 TB710FU Ubuntu 26.04 ARM64")
    if action == "check":
        name = hostname()
        if needs_repair(read_hosts(), name):
            print(f"预检通过：主机名 {name} 未映射到 {LOOPBACK}，应用后将消除 sudo「无法解析主机」警告。")
        else:
            print(f"预检通过：/etc/hosts 已包含 {LOOPBACK} {name}，无需修复。")
        return
    if os.geteuid() != 0:
        raise ValueError("应用及回退需要管理员权限")
    if action == "apply":
        BACKUP.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = BACKUP.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or directory.st_mode & 0o077:
        raise ValueError("回退目录必须由管理员独占")
    with os.fdopen(os.open(BACKUP / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == "rollback":
            rollback()
        else:
            apply(hostname())


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) == 2 else "check")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"终端主机名修复未完成：{error}", file=sys.stderr)
        sys.exit(1)
