#!/bin/bash
set -eu
: "${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件目录}"
exec /usr/bin/python3 "$TB710FU_ATTACHMENTS_DIR/install-on-demand.py" "${TB710FU_FIX_ACTION:-apply}"
