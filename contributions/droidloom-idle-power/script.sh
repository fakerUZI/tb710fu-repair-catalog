#!/bin/bash
set -euo pipefail
exec /bin/bash "${TB710FU_ATTACHMENTS_DIR:?}/install.sh" "${TB710FU_FIX_ACTION:-check}"
