#!/bin/sh
set -eu
export LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin
exec /usr/bin/python3 -I "${TB710FU_ATTACHMENTS_DIR:?}/upgrade.py" "${TB710FU_FIX_ACTION:-check}"
