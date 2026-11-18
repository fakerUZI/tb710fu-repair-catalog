#!/bin/bash
# The sole kernel-upgrade entry. The initial migration accepts undated kernels.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1
: "${TB710FU_ATTACHMENTS_DIR:?修复助手未提供附件目录 TB710FU_ATTACHMENTS_DIR}"
# Preserve existing client action/user variable names.
action=${TB710FU_KERNEL_ACTION:-${TB710FU_SNAP_KERNEL_ACTION:-check}}
caller=${SUDO_USER:-}
if [[ -z $caller && ${PKEXEC_UID:-} =~ ^[0-9]+$ ]]; then
  caller=$(id -nu -- "$PKEXEC_UID")
fi
target=${TB710FU_KERNEL_USER:-${TB710FU_SNAP_USER:-${caller:-ubuntu}}}
[[ $target != root ]] || target=ubuntu
case $action in
  camera-check|camera-install|camera-rollback)
    fix_action=${action#camera-}
    [[ $fix_action != install ]] || fix_action=apply
    exec python3 -B "$TB710FU_ATTACHMENTS_DIR/flash-camera/install-camera-quality.py" "$fix_action" --user "$target"
    ;;
  flash-check|flash-install|flash-rollback)
    fix_action=${action#flash-}
    [[ $fix_action != install ]] || fix_action=apply
    exec python3 -B "$TB710FU_ATTACHMENTS_DIR/flash-camera/install-flash-camera.py" "$fix_action" --user "$target"
    ;;
esac
exec python3 -B "$TB710FU_ATTACHMENTS_DIR/assemble-kernel-bundle.py" \
  --attachments-dir "$TB710FU_ATTACHMENTS_DIR" --action "$action" --user "$target"
