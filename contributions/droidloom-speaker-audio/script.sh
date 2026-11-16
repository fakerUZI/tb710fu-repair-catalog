#!/usr/bin/env bash
# TB710FU: user-level WirePlumber settings for DroidLoom playback.
set -euo pipefail
ACTION=${TB710FU_FIX_ACTION:-check}
CFG=${XDG_CONFIG_HOME:-"$HOME/.config"}/wireplumber/wireplumber.conf.d
DEST=$CFG/99-tb710fu-android-audio.conf
SINK=alsa_output.platform-sound.pro-output-0

die() { echo "错误：$*" >&2; exit 1; }
case "$ACTION" in check|apply|rollback) ;; *) die "TB710FU_FIX_ACTION 必须是 check、apply 或 rollback";; esac
[ "$(id -u)" -ne 0 ] || die "请使用当前图形桌面的普通用户运行，不能使用 sudo。"
for cmd in systemctl pactl wpctl cmp mktemp; do command -v "$cmd" >/dev/null || die "缺少依赖：$cmd"; done
[ -r /proc/device-tree/compatible ] || die "无法读取设备型号。"
tr '\0' '\n' </proc/device-tree/compatible | grep -Fxq 'lenovo,tb710fu' || die "仅适用于 TB710FU。"
systemctl --user is-active --quiet wireplumber || die "当前用户的 WirePlumber 未运行。"
pactl list short sinks | awk '{print $2}' | grep -Fxq "$SINK" || die "未找到预期的 TB710FU 扬声器节点。"
pactl list short sources | awk '{print $2}' | grep -Fxq 'alsa_input.platform-sound.pro-input-2' || die "未找到预期的 TB710FU 麦克风节点。"

if [ "$ACTION" = check ]; then
    echo "设备与用户音频会话检查通过。配置路径：$DEST"
    [ ! -e "$DEST" ] || echo "该路径已有文件；apply 只接受本脚本生成的内容。"
    echo "本步骤未修改文件。使用 TB710FU_FIX_ACTION=apply 安装，rollback 撤销本条规则。"
    exit 0
fi

tmp=$(mktemp "${TMPDIR:-/tmp}/tb710fu-audio.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'EOF'
# TB710FU Android playback: keep the speaker clock ahead of the microphone.
monitor.alsa.rules = [
  { matches = [ { node.name = "alsa_input.platform-sound.pro-input-2" } ]
    actions = { update-props = { priority.driver = 1000 } } }
  { matches = [ { node.name = "alsa_output.platform-sound.pro-output-0" } ]
    actions = { update-props = { priority.driver = 2000 api.alsa.period-size = 256 api.alsa.headroom = 256 } } }
]
EOF
[ ! -L "$DEST" ] || die "配置路径为符号链接，拒绝修改。"
existed=0
if [ -e "$DEST" ]; then existed=1; fi
if [ -e "$DEST" ] && ! cmp -s "$tmp" "$DEST"; then
    die "配置文件已被修改或属于其他工具，拒绝覆盖或删除：$DEST"
fi

# Save per-channel volume and mute: WirePlumber restarts may restore older state.
volume=$(pactl get-sink-volume "$SINK") || die "无法读取音量。"
volumes=$(printf '%s\n' "$volume" | grep -oE '[[:alnum:]_-]+: [0-9]+ /' | awk '{print $2}')
[ -n "$volumes" ] || die "无法解析音量，未修改配置。"
mute=$(pactl get-sink-mute "$SINK" | awk '{print $2}')
case "$mute" in yes|no) ;; *) die "无法读取静音状态。";; esac

if [ "$ACTION" = apply ]; then
    mkdir -p "$CFG"
    install -m 0644 "$tmp" "$DEST"
else
    [ -f "$DEST" ] || { echo "本脚本的规则未安装；其他音频规则不会更改。"; exit 0; }
    rm -- "$DEST"
fi

undo_change() {
    if [ "$existed" -eq 1 ]; then install -m 0644 "$tmp" "$DEST"; else rm -f -- "$DEST"; fi
    systemctl --user restart wireplumber || true
}
if ! systemctl --user restart wireplumber; then
    echo "错误：WirePlumber 重启失败，正在撤销本次文件更改。" >&2
    undo_change
    exit 1
fi
ready=0
for unused in $(seq 1 20); do
    if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fxq "$SINK"; then ready=1; break; fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "错误：扬声器未出现，正在撤销本次文件更改。" >&2
    undo_change
    exit 1
fi
# Shell expansion is intentional: pactl accepts one absolute value per channel.
# Values above are parsed as decimal digits only.
pactl set-sink-volume "$SINK" $volumes || die "无法恢复音量，请检查桌面音量。"
pactl set-sink-mute "$SINK" "$mute" || die "无法恢复静音状态，请检查桌面音量。"
echo "已执行 $ACTION；音频已重连。请播放安卓内容检查，并在必要时重新打开应用。"
echo "本规则仅为该用户生效；其他已有 WirePlumber 规则不由 rollback 删除。"
