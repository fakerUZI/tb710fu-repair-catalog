#!/bin/bash
set -euo pipefail

attachments="${TB710FU_ATTACHMENTS_DIR:?缺少修复助手提供的附件目录}"
case "${TB710FU_FIX_ACTION:-apply}" in
    check|apply|rollback) action="${TB710FU_FIX_ACTION:-apply}" ;;
    *) echo '仅支持 check、apply、rollback。' >&2; exit 2 ;;
esac
exec /usr/bin/python3 -B "$attachments/display-profiles.py" "$action"
