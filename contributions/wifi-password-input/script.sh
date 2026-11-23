#!/bin/bash
set -euo pipefail
exec /usr/bin/python3 -I "${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件}/install.py" "${TB710FU_FIX_ACTION:-apply}"
