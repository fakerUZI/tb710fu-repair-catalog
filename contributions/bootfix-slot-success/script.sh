#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin

attachments=${TB710FU_ATTACHMENTS_DIR:-"$(dirname -- "${BASH_SOURCE[0]}")/attachments"}
case ${TB710FU_FIX_ACTION:-check} in
    check) exec /usr/bin/python3 -IB "$attachments/tb710fu-boot-slot.py" ;;
    apply) exec /usr/bin/python3 -IB "$attachments/tb710fu-boot-slot.py" --apply ;;
    *) echo '此修复支持只读检查和应用。GPT 备份供离线恢复。' >&2; exit 2 ;;
esac
