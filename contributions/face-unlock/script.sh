#!/bin/bash
set -euo pipefail
attachments=${TB710FU_ATTACHMENTS_DIR:?请先在 TB710FU 修复助手下载并校验附件。}
exec /usr/bin/python3 -I "$attachments/install.py" "${TB710FU_FIX_ACTION:-apply}"
