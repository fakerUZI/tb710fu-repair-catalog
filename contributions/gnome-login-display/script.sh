#!/bin/bash
set -euo pipefail

[[ $EUID != 0 ]] || { echo '请在当前 GNOME 桌面以普通用户运行。' >&2; exit 1; }
assets=${TB710FU_ATTACHMENTS_DIR:?请先由修复助手下载附件。}
program=$HOME/.local/libexec/tb710fu-login-display.py
unit=tb710fu-login-display.service
unit_path=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$unit
previous_program=a8f184424d0bf9b6710ecb98d7ac344f11436d27f908711865f59c4eda1ec134
previous_unit=a9727d57b9270e9a17486d5f26ea2c40fe73f3923e60349c5715acb3bdab2af9

compatible_file() {
    local path=$1 source=$2 previous=$3
    if [[ -L $path || ( -e $path && ( ! -f $path || ! -r $path ) ) ]] ||
        { [[ -f $path ]] && ! cmp -s -- "$path" "$source" &&
          [[ $(sha256sum -- "$path" | cut -d ' ' -f 1) != "$previous" ]]; }; then
        echo "已有不同的文件，保留原件：$path" >&2
        exit 1
    fi
}

put() {
    local source=$1 target=$2 mode=$3 tmp
    mkdir -p -- "${target%/*}"
    tmp=$(mktemp "${target%/*}/.tb-login-display.XXXXXXXX")
    if install -m "$mode" -- "$source" "$tmp" && mv -f -- "$tmp" "$target"; then
        return
    fi
    rm -f -- "$tmp"
    return 1
}

case ${TB710FU_FIX_ACTION:-apply} in
    check)
        /usr/bin/python3 -I "$assets/login-display.py" --check
        ;;
    apply)
        compatible_file "$program" "$assets/login-display.py" "$previous_program"
        compatible_file "$unit_path" "$assets/$unit" "$previous_unit"
        /usr/bin/python3 -I "$assets/login-display.py" --check
        backup=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/tb-login-repair.XXXXXXXX")
        changed=no
        was_enabled=no
        systemctl --user is-enabled --quiet "$unit" && was_enabled=yes
        cleanup() {
            local code=$?
            if (( code )) && [[ $changed == yes ]]; then
                if [[ $was_enabled == no ]]; then systemctl --user disable "$unit" || true; fi
                if [[ -f $backup/program ]]; then put "$backup/program" "$program" 0755; else rm -f -- "$program"; fi
                if [[ -f $backup/unit ]]; then put "$backup/unit" "$unit_path" 0644; else rm -f -- "$unit_path"; fi
                systemctl --user daemon-reload || true
            fi
            rm -rf -- "$backup"
        }
        trap cleanup EXIT
        if [[ -f $program ]]; then cp -- "$program" "$backup/program"; fi
        if [[ -f $unit_path ]]; then cp -- "$unit_path" "$backup/unit"; fi
        changed=yes
        put "$assets/login-display.py" "$program" 0755
        put "$assets/$unit" "$unit_path" 0644
        systemctl --user daemon-reload
        systemctl --user enable "$unit"
        echo '已更新登录与解锁显示恢复。请保存工作后注销并重新登录。'
        ;;
    rollback)
        compatible_file "$program" "$assets/login-display.py" "$previous_program"
        compatible_file "$unit_path" "$assets/$unit" "$previous_unit"
        if [[ -f $unit_path ]]; then systemctl --user disable --now "$unit"; fi
        rm -f -- "$unit_path" "$program"
        systemctl --user daemon-reload
        echo '已撤销 GNOME 登录与解锁显示恢复。'
        ;;
    *)
        echo '支持 apply、check、rollback。' >&2
        exit 2
        ;;
esac
