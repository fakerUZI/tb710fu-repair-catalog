#!/bin/bash
set -Eeuo pipefail
umask 077

[[ $EUID == 0 ]] || { echo '需要管理员权限' >&2; exit 1; }
action=${TB710FU_FIX_ACTION:-apply}
source=${TB710FU_ATTACHMENTS_DIR:?缺少修复助手附件}/tb-flash-hardware.py
target=/usr/local/libexec/tb-flash-hardware.py
backup_dir=/var/lib/tb710fu-flash-portable
backup=$backup_dir/original.py
expected=ac5f04188b619d44f51f62c9b071c50e7cb7d8e1f86090f2a881985dc3302553

fail() { echo "$*" >&2; exit 1; }
digest() { sha256sum -- "$1" | cut -d ' ' -f 1; }
[[ -f $source && ! -L $source && $(digest "$source") == "$expected" ]] || fail '修复附件校验失败'
[[ -f $target && ! -L $target && $(stat -c %u -- "$target") == 0 ]] || fail '找不到已安装的补光灯组件'
lock=/run/lock/tb710fu-flash-portable.lock
if [[ $action != check ]]; then
    exec 9>"$lock"
    flock -x 9
fi

replace_helper() {
    local temporary
    temporary=$(mktemp --tmpdir="${target%/*}" .tb-flash-hardware.XXXXXXXX)
    if install -o root -g root -m 0755 -- "$1" "$temporary" && mv -T -- "$temporary" "$target"; then
        return 0
    fi
    rm -- "$temporary"
    return 1
}

case $action in
    check)
        /usr/bin/python3 -B "$source" check
        echo 'TB710FU 闪光灯硬件和内核组件检查通过。'
        ;;
    apply)
        /usr/bin/python3 -B "$source" check
        current=$(digest "$target")
        if [[ $current != "$expected" ]]; then
            [[ ! -e $backup_dir && ! -L $backup_dir ]] || fail '已存在旧备份。请先核对当前补光灯组件。'
            # Keep the shared parent traversable while the backup stays private.
            (umask 022; mkdir -p -- "${backup_dir%/*}")
            mkdir -m 0700 -- "$backup_dir"
            if ! install -o root -g root -m 0700 -- "$target" "$backup"; then
                rm -f -- "$backup"
                rmdir -- "$backup_dir"
                fail '无法备份原始补光灯启动脚本'
            fi
            if ! replace_helper "$source"; then
                rm -- "$backup"
                rmdir -- "$backup_dir"
                fail '无法发布补光灯启动脚本'
            fi
        fi
        /usr/bin/python3 -B "$target" check
        systemctl start tb710fu-flash-hardware.service tb710fu-torch.service
        systemctl is-active --quiet tb710fu-flash-hardware.service
        systemctl is-active --quiet tb710fu-torch.service
        echo '补光灯启动修复已应用，手电筒服务运行正常。'
        /usr/bin/python3 -I "$TB710FU_ATTACHMENTS_DIR/torch-quick-settings.py"
        ;;
    rollback)
        [[ $(digest "$target") == "$expected" ]] || fail '当前组件已被修改，拒绝覆盖'
        [[ -f $backup && ! -L $backup && $(stat -c %u -- "$backup") == 0 ]] || fail '找不到原始脚本备份'
        replace_helper "$backup"
        rm -- "$backup"
        rmdir -- "$backup_dir"
        echo '已恢复原始补光灯启动脚本，下次启动服务时生效。'
        ;;
    *) fail 'TB710FU_FIX_ACTION 仅支持 check、apply 或 rollback' ;;
esac
