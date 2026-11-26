#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C

fail() { printf '错误：%s\n' "$*" >&2; exit 1; }

action=${1:-${TB710FU_FIX_ACTION:-apply}}
[[ $# -le 1 && ( $action == apply || $action == check ) ]] || fail '用法：sudo bash script.sh [apply|check]。回退步骤见条目说明。'
[[ $EUID == 0 ]] || fail '请在修复助手使用管理员权限，或在桌面用户终端使用 sudo。'
if [[ ! -f /usr/lib/python3/dist-packages/UpdateManager/Core/UpdateList.py ]]; then
    echo '系统未安装软件更新器，跳过其权限修复。'
    exit 0
fi

# The GTK assistant uses pkexec. Terminal invocations use sudo.
uid=${PKEXEC_UID:-${SUDO_UID:-}}
[[ $uid =~ ^[1-9][0-9]{0,9}$ ]] || fail '无法确定发起操作的普通用户。'
account=$(getent passwd "$uid") || fail '找不到发起操作的用户。'
IFS=: read -r user _ account_uid _ <<< "$account"
[[ -n $user && $account_uid == "$uid" ]] || fail '用户 UID 核对失败。'

machine_id=/etc/machine-id
[[ -f $machine_id && ! -L $machine_id && -s $machine_id ]] || fail '/etc/machine-id 必须是已初始化的普通文件。'
[[ $(stat -c '%u:%g:%h' -- "$machine_id") == 0:0:1 ]] || fail '/etc/machine-id 必须属于 root:root 且无其他硬链接。'
mountpoint -q -- "$machine_id" && fail '/etc/machine-id 是挂载点，请等待系统将标识写入磁盘。'
mount_status=$?
[[ $mount_status == 32 ]] || fail '无法确定 /etc/machine-id 的挂载状态。'

# Serialize this repair on the existing inode without creating a lock file.
exec {lock_fd}< "$machine_id"
flock -x -w 5 "$lock_fd" || fail '无法取得文件锁，请稍后重试。'

probe() {
    runuser -u "$user" -- /usr/bin/python3 -I -c '
import sys
try:
    open(sys.argv[1], "rb").close()
except PermissionError:
    sys.exit(3)
' "$machine_id" {lock_fd}<&- >/dev/null 2>&1
}

if probe; then
    printf '当前桌面用户可以打开 /etc/machine-id。\n'
    exit 0
else
    probe_status=$?
    [[ $probe_status == 3 ]] || fail '无法以桌面用户身份检查 /etc/machine-id。'
fi
[[ $action == apply ]] || fail '当前桌面用户无权读取 /etc/machine-id。'
if ! command -v getfacl >/dev/null || ! command -v setfacl >/dev/null; then
    fail '需要 acl 软件包（getfacl、setfacl）。'
fi

original_acl=$(getfacl -cEnp -- "$machine_id") || fail '无法读取原有 ACL。'
group_rights=---
user_rights=---
mask=
other_read_entries=0
while IFS=: read -r kind qualifier rights; do
    case "$kind:$qualifier" in
        user:"$uid") user_rights=$rights ;;
        group:) group_rights=$rights ;;
        mask:) mask=$rights ;;
    esac
    # A wider ACL mask must not activate a read grant for another entry.
    case "$kind:$qualifier:$rights" in
        user::*) ;;
        user:"$uid":*) ;;
        user:*:r??|group:*:r??) other_read_entries=1 ;;
    esac
done <<< "$original_acl"

if [[ -z $mask ]]; then
    mask=$group_rights
elif [[ $mask != r* && $other_read_entries == 1 ]]; then
    fail '现有 ACL 屏蔽了其他用户或组的读取权限，需人工检查。'
fi

if ! setfacl -n -m "u:$uid:r${user_rights:1},m::r${mask:1}" -- "$machine_id" || ! probe; then
    # On unsupported filesystems setfacl can change mode bits before failing.
    setfacl -n --set-file=- -- "$machine_id" <<< "$original_acl" || fail '权限修复失败，且自动恢复原 ACL 失败。'
    fail '权限写入或复核失败，已恢复原 ACL。'
fi

printf '已验证当前桌面用户的读取权限。请重新打开 Ubuntu 软件更新器。\n'
