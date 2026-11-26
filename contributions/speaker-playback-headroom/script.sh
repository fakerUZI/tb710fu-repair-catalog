#!/usr/bin/env bash
# TB710FU user-session repair for the built-in speaker's playback buffer.
set -euo pipefail
export LC_ALL=C

action=${TB710FU_FIX_ACTION:-check}
sink=alsa_output.platform-sound.pro-output-0
config=${XDG_CONFIG_HOME:-"$HOME/.config"}/wireplumber/wireplumber.conf.d
rule=$config/98-tb710fu-speaker-headroom.conf
attachments=${TB710FU_ATTACHMENTS_DIR:-"$(dirname -- "${BASH_SOURCE[0]}")/attachments"}
payload=$attachments/98-tb710fu-speaker-headroom.conf

die() { printf '错误：%s\n' "$*" >&2; exit 1; }

case $action in
    check|apply|rollback) ;;
    *) die "操作必须为 check、apply 或 rollback。" ;;
esac

(( EUID != 0 )) || die "请使用当前 Ubuntu 桌面用户运行这项修复。"
[[ -r /proc/device-tree/compatible ]] || die "无法读取设备型号。"
tr '\0' '\n' </proc/device-tree/compatible | grep -Fx 'lenovo,tb710fu' >/dev/null ||
    die "此修复仅适用于 TB710FU。"
grep -Eq '^ID=(ubuntu|"ubuntu")$' /etc/os-release || die "此修复适用于 Ubuntu。"

for cmd in wireplumber pw-dump pactl python3 systemctl timeout flock; do
    command -v "$cmd" >/dev/null || die "缺少依赖：$cmd"
done
wireplumber --version | grep -E '^Linked with libwireplumber 0\.5\.[0-9]+' >/dev/null ||
    die "需要 WirePlumber 0.5。"
systemctl --user is-active --quiet wireplumber.service ||
    die "当前用户的 WirePlumber 未运行。"
[[ -f $payload && -r $payload ]] || die "缺少扬声器配置附件。"
[[ $config == /* ]] || die "XDG_CONFIG_HOME 必须是绝对路径。"

check_owned_rule() {
    [[ ! -L $rule ]] || die "配置路径是符号链接，已保留原文件。"
    if [[ -e $rule ]]; then
        [[ -f $rule ]] && cmp -s -- "$payload" "$rule" ||
            die "发现不同内容的扬声器配置，请先检查：$rule"
    fi
}

speaker_state() {
    timeout 3s pw-dump -N "$sink" | python3 -I -c '
import json
import sys

for node in json.load(sys.stdin):
    props = node.get("info", {}).get("props", {})
    if props.get("node.name") == sys.argv[1] and props.get("media.class") == "Audio/Sink":
        serial = props.get("object.serial")
        if serial is not None:
            print(serial, props.get("api.alsa.headroom", "default"))
            sys.exit(0)
sys.exit(1)
' "$sink"
}

if [[ $action == check ]]; then
    check_owned_rule
    state=$(speaker_state) || die "未找到 TB710FU 内置扬声器。"
    printf '内置扬声器检查通过，当前缓冲余量：%s（帧）。\n' "${state#* }"
    if [[ -f $rule ]]; then
        printf '配置文件已安装：%s\n' "$rule"
    fi
    exit 0
fi

runtime=${XDG_RUNTIME_DIR:-/run/user/$EUID}
[[ $runtime == /* && -d $runtime && -O $runtime ]] ||
    die "请从当前 Ubuntu 桌面会话运行。"
exec 9>"$runtime/tb710fu-speaker-headroom.lock"
flock -n 9 || die "已有另一个扬声器修复操作在运行。"
check_owned_rule
if [[ $action == rollback && ! -f $rule ]]; then
    echo "扬声器缓冲配置未安装。"
    exit 0
fi

original_state=$(speaker_state 2>/dev/null) || original_state=
if [[ -z $original_state && $action == apply ]]; then
    die "未找到 TB710FU 内置扬声器。"
fi

if [[ -n $original_state ]]; then
    volume=$(pactl get-sink-volume "$sink") || die "无法读取内置扬声器音量。"
    stereo_volume='front-left: ([0-9]+) /.*front-right: ([0-9]+) /'
    [[ $volume =~ $stereo_volume ]] || die "无法解析扬声器的两个声道音量。"
    levels=("${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}")
    mute=$(pactl get-sink-mute "$sink") || die "无法读取内置扬声器静音状态。"
    case $mute in
        'Mute: yes') mute=1 ;;
        'Mute: no') mute=0 ;;
        *) die "无法解析扬声器静音状态。" ;;
    esac
fi

restart_manager() {
    timeout 20s systemctl --user restart wireplumber.service
}

wait_for_speaker() {
    local mode=$1 previous_serial=$2 state serial headroom attempt
    for ((attempt=0; attempt<25; attempt++)); do
        if state=$(speaker_state 2>/dev/null); then
            read -r serial headroom <<< "$state"
            if [[ $serial != "$previous_serial" && ( $mode != apply || $headroom == 960 ) ]] &&
               timeout 3s pactl get-sink-mute "$sink" >/dev/null 2>&1; then
                return 0
            fi
        fi
        sleep 0.4
    done
    return 1
}

restore_levels() {
    [[ -n $original_state ]] || return 0
    if [[ $mute == 1 ]]; then
        pactl set-sink-mute "$sink" 1 || return 1
    fi
    pactl set-sink-volume "$sink" "${levels[@]}" || return 1
    if [[ $mute == 0 ]]; then
        pactl set-sink-mute "$sink" 0 || return 1
    fi
}

mkdir -p -- "$config"
stage=$(mktemp -d -- "$config/.tb710fu-speaker.XXXXXXXX")
committed=0
restart_attempted=0

cleanup() {
    local status=$? keep_stage=0 undo_serial=
    trap - EXIT
    if (( ! committed )); then
        if [[ $rule -ef $stage/new ]]; then
            rm -- "$rule"
        elif [[ -f $stage/old ]]; then
            if ! ln -T -- "$stage/old" "$rule"; then
                printf '原配置保存在：%s\n' "$stage/old" >&2
                keep_stage=1
            fi
        fi
        if (( restart_attempted )); then
            undo_serial=$(speaker_state 2>/dev/null) || undo_serial=
            if ! restart_manager ||
               ! wait_for_speaker rollback "${undo_serial%% *}" ||
               ! restore_levels; then
                echo "恢复音频会话失败，请检查桌面声音设置。" >&2
            fi
        fi
    fi
    if (( ! keep_stage )); then
        rm -rf -- "$stage"
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ $action == rollback ]]; then
    mv -T -- "$rule" "$stage/old"
elif [[ ! -e $rule ]]; then
    install -m 0644 -- "$payload" "$stage/new"
    ln -T -- "$stage/new" "$rule"
fi

restart_attempted=1
restart_manager &&
    wait_for_speaker "$action" "${original_state%% *}" &&
    restore_levels || die "配置未成功加载或无法恢复音量，正在恢复原配置。"

committed=1
if [[ $action == apply ]]; then
    echo "已启用内置扬声器 960 帧缓冲余量，可用原歌曲复测。"
else
    echo "已移除该扬声器缓冲规则，其他音频规则仍由 WirePlumber 加载。"
fi
