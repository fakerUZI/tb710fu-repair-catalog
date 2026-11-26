#!/bin/bash
set -euo pipefail
source_hook=${TB710FU_ATTACHMENTS_DIR:?}/tb710fu-pogo-sleep
target=/usr/lib/systemd/system-sleep/tb710fu-pogo-sleep
dev=/sys/bus/platform/devices/pogo-keyboard
action=${TB710FU_FIX_ACTION:-check}
die() { echo "$*" >&2; exit 1; }
case "$action" in
    check|apply|rollback) ;;
    *) die "支持 check、apply 和 rollback。" ;;
esac
[ -f "$source_hook" ] || die "缺少挂起钩子附件。"
if [ -e "$target" ] || [ -L "$target" ]; then
    [ -f "$target" ] && [ ! -L "$target" ] && cmp -s "$source_hook" "$target" || die "已有其他版本的挂起钩子，请先核对。"
fi
if [ "$action" = rollback ]; then
    if [ -f "$target" ]; then
        [ "$EUID" = 0 ] || die "撤销需要管理员权限。"
        /bin/sh "$target" post suspend
        rm -- "$target"
    fi
    echo "已撤销 Pogo 挂起修复。"
    exit 0
fi
[ "$(basename "$(readlink -f "$dev/driver")")" = 'lenovo,lenovo_keyboard' ] || die "设备未绑定支持的 Pogo 驱动。"
for attr in kb_hall_status kb_connect_status kb_power_status power/wakeup; do
    [ -r "$dev/$attr" ] || die "缺少 Pogo 驱动接口 $attr。"
done
if [ -x "$target" ]; then echo "已安装且已启用：Pogo 挂起唤醒修复。"; exit 0; fi
if [ "$action" = check ]; then echo "可以应用：当前 Pogo 驱动符合要求。"; exit 0; fi
[ "$EUID" = 0 ] || die "安装需要管理员权限。"
tmp=$(mktemp "${target}.XXXXXX")
trap 'rm -f "$tmp"' EXIT
install -m755 "$source_hook" "$tmp"
mv -f -- "$tmp" "$target"
echo "已安装 Pogo 挂起唤醒修复，下次挂起生效。"
