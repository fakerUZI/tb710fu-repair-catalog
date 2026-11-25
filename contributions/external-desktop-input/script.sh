#!/bin/bash
set -euo pipefail
action=${1:-${TB710FU_FIX_ACTION:-check}}
assets=${TB710FU_ATTACHMENTS_DIR:-$(dirname -- "$(realpath -- "$0")")/attachments}
exec /usr/bin/python3 -I "$assets/manage.py" "$action"
