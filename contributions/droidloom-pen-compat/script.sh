#!/bin/bash
set -euo pipefail
assets=${TB710FU_ATTACHMENTS_DIR:?请先通过修复助手下载附件。}
exec /usr/bin/python3 -IB "$assets/pen-compat.py" "${TB710FU_FIX_ACTION:-apply}"
