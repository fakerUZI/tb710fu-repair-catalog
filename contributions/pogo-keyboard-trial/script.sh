#!/bin/bash
set -euo pipefail

attachments=${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件}
case ${TB710FU_FIX_ACTION:-check} in
    apply) set -- --start ;;
    check) set -- ;;
    rollback)
        printf '%s\n' '测试到时会自动清理。若平板卡死，重启后测试模块不会自动加载。'
        exit 0
        ;;
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
' "$attachments/pogo_trial.py" "$@"
