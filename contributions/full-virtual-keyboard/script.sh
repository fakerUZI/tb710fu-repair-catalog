#!/usr/bin/env bash
set -euo pipefail
attachments=${TB710FU_ATTACHMENTS_DIR:-"$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/attachments"}
exec /usr/bin/python3 "$attachments/install.py" "${TB710FU_FIX_ACTION:-check}"
