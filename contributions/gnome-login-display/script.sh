#!/bin/bash
set -euo pipefail

[[ $EUID != 0 ]] || { echo '请在当前 GNOME 桌面以普通用户运行。' >&2; exit 1; }
assets=${TB710FU_ATTACHMENTS_DIR:?请先由修复助手下载附件。}
program=$HOME/.local/libexec/tb710fu-login-display.py
unit=tb710fu-login-display.service
unit_path=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$unit

compatible_file() {
    local path=$1 source=$2
    if [[ -L $path || ( -e $path && ( ! -f $path || ! -r $path ) ) ]] ||
        { [[ -f $path ]] && ! cmp -s -- "$path" "$source"; }; then
        echo "已有不同的文件，保留原件：$path" >&2
        exit 1
    fi
}

case ${TB710FU_FIX_ACTION:-apply} in
    check)
        /usr/bin/python3 -I "$assets/login-display.py" --check
        ;;
    apply)
        compatible_file "$program" "$assets/login-display.py"
        compatible_file "$unit_path" "$assets/$unit"
        /usr/bin/python3 -I "$assets/login-display.py" --check
        new_program=no
        new_unit=no
        cleanup() {
            local code=$?
            if (( code )); then
                if [[ $new_unit == yes ]]; then
                    systemctl --user disable "$unit" || true
                    rm -f -- "$unit_path"
                fi
                if [[ $new_program == yes ]]; then rm -f -- "$program"; fi
                systemctl --user daemon-reload || true
            fi
        }
        trap cleanup EXIT
        if [[ ! -f $program ]]; then
            new_program=yes
            mkdir -p -- "${program%/*}"
            install -m 0755 -- "$assets/login-display.py" "$program"
        fi
        if [[ ! -f $unit_path ]]; then
            new_unit=yes
            mkdir -p -- "${unit_path%/*}"
            install -m 0644 -- "$assets/$unit" "$unit_path"
        fi
        systemctl --user daemon-reload
        systemctl --user enable "$unit"
        echo '已安装登录显示恢复。请保存工作后注销并重新登录。'
        ;;
    rollback)
        compatible_file "$program" "$assets/login-display.py"
        compatible_file "$unit_path" "$assets/$unit"
        if [[ -f $unit_path ]]; then systemctl --user disable --now "$unit"; fi
        rm -f -- "$unit_path" "$program"
        systemctl --user daemon-reload
        echo '已撤销 GNOME 登录显示恢复。'
        ;;
    *)
        echo '支持 apply、check、rollback。' >&2
        exit 2
        ;;
esac
