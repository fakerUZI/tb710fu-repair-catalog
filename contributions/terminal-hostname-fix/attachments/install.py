"""Append the running hostname to /etc/hosts so sudo stops reporting unresolvable hosts."""
import base64
import fcntl
import hashlib
import json
import os
import re
import socket
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
    return f"{LOOPBACK}\t{name}\n"


def read_hosts():
    if HOSTS.is_symlink() or not HOSTS.is_file():
        raise ValueError("/etc/hosts 不是常规文件，请先人工检查")
    return HOSTS.read_text(encoding="utf-8", errors="strict")


def find_hostname_tokens(name):
    tokens = []
    for number, line in enumerate(read_hosts().splitlines(), 1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        fields = stripped.split()
        if name in fields[1:]:
            tokens.append((number, fields[0]))
    return tokens


def preflight(action):
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    model = Path("/sys/firmware/devicetree/base/model")
    if (os.uname().machine != "aarch64" or not model.is_file() or "TB710FU" not in model.read_text()
            or release.get("ID") != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "26.04"):
        raise ValueError("适用于 TB710FU Ubuntu 26.04 ARM64")
    name = hostname()
    tokens = find_hostname_tokens(name)
    for number, address in tokens:
        if address != LOOPBACK:
            raise ValueError(f"/etc/hosts 第 {number} 行已把 {name} 指向 {address}，请先人工确认")
    if action != "check" and os.geteuid() != 0:
        raise ValueError("应用及回退需要管理员权限")
    return name, bool(tokens)


def write_hosts(new_text, expected_hash):
    current = read_hosts()
    if hashlib.sha256(current.encode()).hexdigest() != expected_hash:
        raise ValueError("/etc/hosts 在预检后被修改，请重新执行")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=HOSTS.parent,
                                     prefix="hosts.", delete=False) as stream:
        stream.write(new_text)
        stream.flush()
        os.chmod(stream.name, 0o644)
        os.replace(stream.name, HOSTS)


def load_state(path):
    before = json.loads(path.read_text())
    if before.get("format") != 1 or not isinstance(before.get("sha256"), str):
        raise ValueError("无法识别本机回退记录")
    return before


def apply(name, already):
    if already:
        print(f"/etc/hosts 已包含 {LOOPBACK} 到 {name} 的映射，无需修改。")
        return
    if BACKUP.is_symlink() or (BACKUP / "before.json").is_symlink():
        raise ValueError("回退目录必须由管理员直接管理")
    BACKUP.mkdir(mode=0o700, exist_ok=True)
    state_path = BACKUP / "before.json"
    if not state_path.exists():
        original = read_hosts()
        before = {"format": 1, "sha256": hashlib.sha256(original.encode()).hexdigest(),
                  "content": base64.b64encode(original.encode()).decode()}
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(before) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, state_path)
    else:
        load_state(state_path)
    current = read_hosts()
    digest = hashlib.sha256(current.encode()).hexdigest()
    try:
        write_hosts(current + entry_line(name), digest)
    except Exception:
        try:
            rollback()
        except Exception as error:
            print("自动回退中断，快照仍保留：", error, file=sys.stderr)
        raise
    print(f"已向 /etc/hosts 追加 {LOOPBACK} {name}。终端不会再出现「无法解析主机」警告。")


def rollback():
    state_path = BACKUP / "before.json"
    if not state_path.is_file():
        raise ValueError("没有本条目写入的回退记录")
    before = load_state(state_path)
    original = base64.b64decode(before["content"]).decode()
    write_hosts(original, before["sha256"])
    state_path.unlink()
    print("已恢复原 /etc/hosts。")


def main(action):
    if action not in {"check", "apply", "rollback"}:
        raise ValueError("操作须为 check、apply 或 rollback")
    name, already = preflight(action)
    if action == "check":
        if already:
            print(f"预检通过：/etc/hosts 已包含 {LOOPBACK} {name}，无需修复。")
        else:
            print(f"预检通过：主机名 {name} 未映射到 {LOOPBACK}，应用后将消除 sudo「无法解析主机」警告。")
        return
    if action == "rollback":
        rollback()
        return
    apply(name, already)


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) == 2 else "check")
    except (OSError, ValueError, RuntimeError, UnicodeError, json.JSONDecodeError) as error:
        print(f"终端主机名修复未完成：{error}", file=sys.stderr)
        sys.exit(1)
