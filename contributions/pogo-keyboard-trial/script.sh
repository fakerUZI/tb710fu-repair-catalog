#!/bin/bash
set -euo pipefail

attachments=${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件}
action=${TB710FU_FIX_ACTION:-check}
case $action in
    apply|check|rollback) ;;
    *) printf '%s\n' '支持 apply、check 和 rollback。' >&2; exit 2 ;;
esac

exec /usr/bin/python3 -IBu -c '
from pathlib import Path
import runpy
import sys

script = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(script.parent))
sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name="__main__")
' "$attachments/pogo_pen_install.py" "$action"
