#!/usr/bin/env bash
set -euo pipefail
: "${TB710FU_ATTACHMENTS_DIR:?请由修复助手提供附件目录，或手动设置 TB710FU_ATTACHMENTS_DIR}"
case "${TB710FU_FIX_ACTION:-check}" in
  check|apply|rollback) ;;
  *) echo 'TB710FU_FIX_ACTION 仅支持 check、apply、rollback' >&2; exit 2 ;;
esac
exec python3 "$TB710FU_ATTACHMENTS_DIR/repair.py" "${TB710FU_FIX_ACTION:-check}"
