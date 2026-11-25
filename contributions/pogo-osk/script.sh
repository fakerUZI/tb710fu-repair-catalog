#!/bin/bash
set -euo pipefail
action=${1:-${TB710FU_FIX_ACTION:-check}}
assets=${TB710FU_ATTACHMENTS_DIR:-$(dirname -- "$(realpath -- "$0")")/attachments}
path=/usr/share/gnome-shell/extensions/tb-osk-input-policy@tb710fu/extension.js
case $action in check|apply|rollback) ;; *) exit 2 ;; esac
[[ $(realpath -e -- "$path") == "$path" && -f "$path" && $(stat -c %u -- "$path") == 0 ]] || { echo '未找到支持的 TB710FU 屏幕键盘扩展。' >&2; exit 1; }
args=(--force --silent --fuzz=0 --no-backup-if-mismatch --reject-file=/dev/null)
opposite=(--reverse)
if [[ $action == rollback ]]; then args+=(--reverse); opposite=(); fi
if [[ $action != check ]]; then
    [[ $EUID == 0 ]] || { echo '安装和回退需要管理员权限。' >&2; exit 1; }
    # The Wi-Fi password repair edits the same GNOME extension.
    exec 9>>/run/lock/tb710fu-wifi-password.lock
    flock -x 9
fi
patchfile=$assets/input-policy.patch
if ! patch "${args[@]}" --dry-run "$path" "$patchfile" >/dev/null; then
    if patch --force --silent --fuzz=0 --dry-run "${opposite[@]}" "$path" "$patchfile" >/dev/null; then
        echo '当前扩展已处于目标状态。'
        exit 0
    fi
    echo '现有扩展与补丁不匹配，无法安全修改。' >&2
    exit 1
fi
if [[ $action == check ]]; then echo '可以安装磁吸键盘屏蔽策略。'; exit 0; fi
temp=$(mktemp -- "${path%/*}/.pogo-osk.XXXXXXXX")
trap 'rm -f -- "$temp"' EXIT
patch "${args[@]}" --output="$temp" "$path" "$patchfile"
chmod --reference="$path" "$temp"
chown --reference="$path" "$temp"
mv -T -- "$temp" "$path"
echo '屏幕键盘策略已更新，重新登录 GNOME 后载入。'
