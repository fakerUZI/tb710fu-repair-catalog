"""Read the official Arch ARM64 package database without installing any code."""

import gzip
import io
import json
from pathlib import Path
import re
import tarfile
from urllib.request import urlopen

INDEX = "https://denialwm.github.io/droidloom/aarch64/droidloom.db.tar.gz"
METADATA = Path("/usr/share/droidloom/package.json")
COMPONENTS = ("droidloom-runtime", "droidloom-image", "droidloom-base")
LIMIT = 1 << 20


def version_key(version):
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}-[1-9][0-9]*", version):
        raise ValueError("无法识别 Droidloom 软件包版本。")
    return tuple(map(int, re.split(r"[.-]", version)))


def installed_version(path=METADATA):
    info = json.loads(path.read_text())
    if not isinstance(info, dict) or info.get("architecture") != "aarch64" or type(info.get("release")) is not int:
        raise ValueError("无法识别本机 Droidloom ARM64 软件包。")
    version = f"{info.get('version')}-{info['release']}"
    if version_key(version) < (0, 1, 0, 42):
        raise ValueError("本机 Droidloom 需要先迁移到 0.1.0-42 或更高版本。")
    return version


def read_index(raw):
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        data = compressed.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Droidloom 软件包索引过大。")
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for entry in archive:
            if not entry.isfile() or not entry.name.endswith("/desc"):
                continue
            fields = {}
            for block in archive.extractfile(entry).read().decode().strip().split("\n\n"):
                key, *values = block.splitlines()
                fields[key] = values
            def single(key):
                values = fields.get(key, [])
                if len(values) != 1:
                    raise ValueError(f"Droidloom 索引字段异常 {key}")
                return values[0]
            name = single("%NAME%")
            if name not in COMPONENTS:
                continue
            version = single("%VERSION%")
            version_key(version)
            filename = f"{name}-{version}-aarch64.pkg.tar.zst"
            digest = single("%SHA256SUM%")
            size = int(single("%CSIZE%"))
            if (name in entries or single("%ARCH%") != "aarch64" or single("%FILENAME%") != filename
                    or not re.fullmatch("[a-f0-9]{64}", digest) or not 0 < size < 2**31):
                raise ValueError(f"Droidloom 软件包索引不一致 {name}")
            entries[name] = (version, filename, size, digest, fields.get("%DEPENDS%", []))
    if set(entries) != set(COMPONENTS):
        raise ValueError("Droidloom 软件包索引缺少组件。")
    runtime, image, base = (entries[name] for name in COMPONENTS)
    if (runtime[0] != image[0] or f"droidloom-image={image[0]}" not in runtime[4]
            or f"droidloom-base={base[0]}" not in image[4]):
        raise ValueError("Droidloom 软件包依赖版本不匹配。")
    return runtime[0], [(entry[1], entry[2], entry[3]) for entry in (runtime, image, base)]


def latest():
    with urlopen(INDEX, timeout=12) as response:
        if response.geturl() != INDEX:
            raise ValueError("官方索引地址发生变化。")
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Droidloom 软件包索引过大。")
    try:
        return read_index(raw)
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError("无法读取 Droidloom 软件包索引。") from error
