#!/bin/bash
set -euo pipefail
assets=${TB710FU_ATTACHMENTS_DIR:-$(dirname -- "$(realpath -- "$0")")/attachments}
exec /usr/bin/python3 -IB "$assets/install.py" "${1:-${TB710FU_FIX_ACTION:-check}}"
