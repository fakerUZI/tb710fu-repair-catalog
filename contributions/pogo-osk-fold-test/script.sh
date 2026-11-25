#!/bin/bash
set -euo pipefail
# Fold beta 2 installs the device-bound evdev posture reader.
action=${1:-${TB710FU_FIX_ACTION:-check}}
assets=${TB710FU_ATTACHMENTS_DIR:-$(dirname -- "$(realpath -- "$0")")/attachments}
exec /usr/bin/python3 -I "$assets/install.py" "$action"
