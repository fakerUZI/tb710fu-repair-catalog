#!/bin/bash
set -euo pipefail

attachments=${TB710FU_ATTACHMENTS_DIR:?请先在 TB710FU 修复助手下载并校验附件。}
action=${TB710FU_FIX_ACTION:-check}
case "$action" in
    check|apply) exec /usr/bin/python3 -I "$attachments/install.py" "$action" ;;
    rollback) exec /usr/bin/python3 -I "$attachments/install.py" rollback ;;
    *) printf '%s\n' 'TB710FU_FIX_ACTION 只接受 check、apply 或 rollback。' >&2; exit 2 ;;
esac
