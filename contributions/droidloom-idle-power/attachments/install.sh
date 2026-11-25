#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
lib=/usr/local/libexec/tb710fu-droidloom-power
units=/etc/systemd/system
idle=tb710fu-droidloom-idle.service
socket=tb710fu-droidloom-inspect.socket
profile=tb710fu-profile-tuning.service
action=${1:-install}
die() { echo "$*" >&2; exit 1; }
case "$action" in
    check) ;;
    apply|install|rollback) [ "$(id -u)" = 0 ] || die "请通过修复助手或 sudo 运行。" ;;
    *) die "支持 check、apply 和 rollback。" ;;
esac
grep -zqx 'lenovo,tb710fu' /proc/device-tree/compatible || die "不支持的设备。"
[ -f /etc/droidloom/cell.json ] && [ -x /usr/bin/droidloomctl ] || die "请先安装 DroidLoom。"
uid=$(python3 -Ic 'import json; print(int(json.load(open("/etc/droidloom/cell.json"))["host_uid"]))')
[ "$uid" -ge 1000 ] || die "无法识别 DroidLoom 桌面用户。"
user=$(id -nu "$uid")
home_dir=$(getent passwd "$uid" | cut -d: -f6)
as_user() {
    if [ "$EUID" = "$uid" ]; then "$@"; else
        runuser -u "$user" -- env HOME="$home_dir" XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" "$@"
    fi
}
uctl() { as_user systemctl --user "$@"; }
launcher="$home_dir/.local/bin/droidloom-launch"
backup="$home_dir/.local/state/tb710fu-droidloom-power/launch.before"
dropin="$units/$socket.d"
owner="$dropin/owner.conf"
factory=b2b918c0df158bb219cfd22df7097569284471f8db15e3d2fd7cd6db4f550db2
hash_user() { as_user sha256sum "$1" | cut -d' ' -f1; }
want_owner() { printf '[Socket]\nSocketUser=%s\n' "$uid"; }
sources=(inspect-cell.py idle-stop.py power-policy.py "$socket" tb710fu-droidloom-inspect@.service "$profile" "$idle")
targets=("$lib/inspect-cell.py" "$lib/idle-stop.py" "$lib/power-policy.py" "$units/$socket" "$units/tb710fu-droidloom-inspect@.service" "$units/$profile" "/etc/systemd/user/$idle")
uctl show-environment >/dev/null || die "请从 DroidLoom 用户的桌面会话运行。"
ready=true
for i in "${!sources[@]}"; do
    dest=${targets[i]}
    [ -f "${sources[i]}" ] || die "缺少附件：${sources[i]}"
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "${sources[i]}" "$dest" || die "同名文件已有其他内容：$dest"
    else
        ready=false
    fi
done
if [ -e "$owner" ] || [ -L "$owner" ]; then
    [ ! -L "$owner" ] && [ -f "$owner" ] && want_owner | cmp -s - "$owner" || die "套接字归属配置已被修改。"
else
    ready=false
fi
[ -f "$launcher" ] && [ ! -L "$launcher" ] || die "需要 BOOTFIX 的 DroidLoom 按需启动器。"
if [ -e "$backup" ] || [ -L "$backup" ]; then
    [ -f "$backup" ] && [ ! -L "$backup" ] && [ "$(hash_user "$backup")" = "$factory" ] || die "原启动器备份校验失败。"
fi
if cmp -s launch "$launcher"; then
    [ -x "$launcher" ] || ready=false
    [ -f "$backup" ] || die "已安装的启动器缺少原版备份。"
elif [ "$(hash_user "$launcher")" = "$factory" ]; then
    ready=false
else
    die "当前按需启动器与支持的版本不同。"
fi
services_ready() {
    systemctl is-enabled --quiet "$socket" && systemctl is-active --quiet "$socket" &&
    systemctl is-enabled --quiet "$profile" && systemctl is-active --quiet "$profile" &&
    [[ " $(uctl show droidloom.service -p Wants --value) " == *" $idle "* ]] &&
    { ! uctl is-active --quiet droidloom.service || uctl is-active --quiet "$idle"; }
}
if [ "$action" != rollback ]; then
    if "$ready" && services_ready; then
        echo "已安装且已启用：DroidLoom 空闲 120 秒关停及平衡、节能调度。"
        exit 0
    fi
    /usr/bin/python3 -I -c 'import runpy; p=runpy.run_path("power-policy.py"); [p["limits"](mode) for mode in p["LIMITS"]]'
    if [ "$action" = check ]; then echo "可以应用：设备与按需启动器符合要求，修复尚未完整启用。"; exit 0; fi
fi
if [ "$action" = rollback ]; then
    if [ -f "/etc/systemd/user/$idle" ]; then uctl stop "$idle"; uctl disable "$idle"; fi
    for unit in "$socket" "$profile"; do
        if [ -f "$units/$unit" ]; then systemctl disable --now "$unit"; fi
    done
    if cmp -s launch "$launcher"; then as_user cp -p "$backup" "$launcher"; fi
    rm -f "$owner" "${targets[@]}"
    for dir in "$lib" "$dropin"; do
        if [ -d "$dir" ] && [ -z "$(ls -A "$dir")" ]; then rmdir "$dir"; fi
    done
    systemctl daemon-reload
    uctl daemon-reload
    echo "已撤销空闲关停和电源调度，原按需启动器可继续使用。"
    exit 0
fi
if [ ! -e "$backup" ]; then
    as_user mkdir -p "$(dirname "$backup")"
    as_user cp -p "$launcher" "$backup"
fi
install -d "$lib" "$dropin" /etc/systemd/user
for i in "${!sources[@]}"; do install -m644 "${sources[i]}" "${targets[i]}"; done
want_owner > "$owner"
# Batch execution stages attachments in a root-only directory. Pass the verified
# launcher over stdin and write into HOME with the desktop user's privileges.
as_user /bin/sh -ec '
    tmp=$(mktemp "$1.XXXXXX")
    trap '\''rm -f "$tmp"'\'' EXIT
    cat > "$tmp"
    chmod 755 "$tmp"
    mv -f "$tmp" "$1"
' sh "$launcher" < launch
systemctl daemon-reload
systemctl enable --now "$socket" "$profile"
uctl daemon-reload
uctl add-wants droidloom.service "$idle"
if uctl is-active --quiet droidloom.service; then uctl restart "$idle"; fi
services_ready || die "服务未完整启用，请检查 systemd 日志。"
echo "已启用 DroidLoom 空闲关停和电源调度。"
