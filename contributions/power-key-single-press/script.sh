#!/bin/bash
# check 只读；apply/rollback 保存并恢复完整用户级快照。
set -eu
: "${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件目录}"
exec /usr/bin/python3 "$TB710FU_ATTACHMENTS_DIR/install-power-key.py" "${TB710FU_FIX_ACTION:-apply}"
