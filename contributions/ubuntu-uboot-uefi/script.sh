#!/bin/bash
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
exec /usr/bin/python3 -I "${TB710FU_ATTACHMENTS_DIR:?}/migrate.py" "${TB710FU_FIX_ACTION:-check}"
