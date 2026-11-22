"""Launch the installed one-click upgrade with batch authorization support."""

import os
from pathlib import Path
import sys

ASSISTANT = Path("/usr/share/tb710fu-repair-assistant")
HELPER = Path("/usr/libexec/tb710fu-repair-assistant-run-batch")

if __name__ == "__main__":
    launcher = ASSISTANT / "one-click-upgrade.py"
    if not launcher.is_file() or not HELPER.is_file():
        sys.exit("请先通过「更新修复助手」安装 0.10.15 或更新版本。")
    os.execv("/usr/bin/python3", ["/usr/bin/python3", "-IBu", str(launcher), *sys.argv[1:]])
