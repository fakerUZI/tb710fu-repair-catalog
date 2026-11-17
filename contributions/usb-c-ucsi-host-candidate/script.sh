#!/usr/bin/env bash
# Candidate for issue #19. No persistent install or block-device writes.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
dir=${TB710FU_ATTACHMENTS_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/attachments" && pwd)}
action=${TB710FU_FIX_ACTION:-check}
case "$action" in
  check|watch|host|trace) ;;
  apply) action=host ;;
  *) echo '支持 check、watch、trace、host；没有持久安装需要 rollback。' >&2; exit 2 ;;
esac
exec python3 "$dir/usb-c-test.py" "$action" "$@"
