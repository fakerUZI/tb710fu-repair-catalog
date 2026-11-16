#!/usr/bin/env bash
# Standalone default is read-only; the repair assistant explicitly passes action.
set -euo pipefail
: "${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件目录}"
exec python3 "$TB710FU_ATTACHMENTS_DIR/repair.py" "${TB710FU_FIX_ACTION:-check}"
