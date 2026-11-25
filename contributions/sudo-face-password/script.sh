#!/bin/bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
exec /usr/bin/python3 -I "${TB710FU_ATTACHMENTS_DIR:?请先在修复助手下载附件。}/install.py" "${TB710FU_FIX_ACTION:-apply}"
