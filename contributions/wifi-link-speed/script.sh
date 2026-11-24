#!/usr/bin/env bash
set -euo pipefail

attachments=${TB710FU_ATTACHMENTS_DIR:-"$(dirname -- "${BASH_SOURCE[0]}")/attachments"}
exec /usr/bin/python3 -I "$attachments/install.py" "${TB710FU_FIX_ACTION:-check}"
