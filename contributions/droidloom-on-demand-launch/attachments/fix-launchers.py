#!/usr/bin/python3
from pathlib import Path
import os
import tempfile

folder = Path.home() / ".local/share/applications"
old = "Exec=/usr/bin/droidloomctl launch "
new = f"Exec={Path.home()}/.local/bin/droidloom-launch "
failed = False
for path in folder.glob("droidloom-*.desktop"):
    try:
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_text(encoding="utf-8")
        if "X-Droidloom-Managed=true\n" not in data or old not in data:
            continue
        updated = data.replace(old, new, 1)
        fd, tmp = tempfile.mkstemp(prefix=".droidloom-launcher-", dir=folder)
        try:
            os.fchmod(fd, path.stat().st_mode & 0o777)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(updated)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except (OSError, UnicodeError) as exc:
        failed = True
        print(f"{path}: {exc}", file=__import__("sys").stderr)
if failed:
    raise SystemExit(1)
