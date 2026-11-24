#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C

attachments=${TB710FU_ATTACHMENTS_DIR:-"$(dirname -- "${BASH_SOURCE[0]}")/attachments"}
exec /usr/bin/python3 -I "$attachments/install.py" "${TB710FU_FIX_ACTION:-check}"
