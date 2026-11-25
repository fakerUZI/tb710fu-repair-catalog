#!/bin/bash
# Ubuntu / NetworkManager. Without arguments this only reports the current state.
set -eu
export LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin
nm=org.freedesktop.NetworkManager
obj=/org/freedesktop/NetworkManager
prop() { busctl --system get-property "$nm" "$obj" "$nm" "$1"; }
action=${1:-${TB710FU_FIX_ACTION:-check}}
case "$action" in check|apply) ;; *) echo "用法：bash script.sh [check|apply]" >&2; exit 2;; esac
state=$(nmcli networking connectivity)
printf '全局连通性：%s\n' "$state"
for key in ConnectivityCheckAvailable ConnectivityCheckEnabled ConnectivityCheckUri; do
    printf '%s = %s\n' "$key" "$(prop "$key")"
done
primary=$(prop PrimaryConnection)
primary=${primary#o \"}; primary=${primary%\"}
type=none
if [[ "$primary" != / ]]; then
    type=$(busctl --system get-property "$nm" "$primary" "$nm.Connection.Active" Type)
fi
printf '主连接类型：%s\n' "$type"
while IFS= read -r iface; do
    echo "Wi-Fi 接口：$iface"
    nmcli -f GENERAL.IP4-CONNECTIVITY,GENERAL.IP6-CONNECTIVITY device show "$iface"
done < <(nmcli -t -f DEVICE,TYPE,STATE device | awk -F: '$2=="wifi" && $3=="connected" {print $1}')
[[ "$action" == apply ]] || exit 0
[[ "$type" == 's "802-11-wireless"' ]] || { echo '当前主连接不是 Wi-Fi，请在出现问号的 Wi-Fi 上运行。'; exit 1; }
case "$state" in
    full) echo '主连接当前已报告 FULL，已跳过配置更改。'; exit 0;;
    portal) echo '请先完成此 Wi-Fi 的网页认证，再复检。'; exit 1;;
    limited|unknown) ;;
    *) echo '请先连接有默认路由的 Wi-Fi。'; exit 1;;
esac
(( EUID == 0 )) || { echo '应用修复请运行 sudo bash script.sh apply。'; exit 1; }
if [[ $(prop ConnectivityCheckUri) == 's ""' ]]; then
    echo '正在加载 Ubuntu 官方探测地址。'
    if [[ $(dpkg-query -W -f='${db:Status-Status}' network-manager-config-connectivity-ubuntu 2>/dev/null || :) != installed ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get -y --no-remove --no-install-recommends install network-manager-config-connectivity-ubuntu
    fi
    nmcli general reload conf
fi
if [[ $(prop ConnectivityCheckAvailable) != 'b true' || $(prop ConnectivityCheckUri) == 's ""' ]]; then
    echo '探测服务仍不可用。请检查 NetworkManager --print-config 的 [connectivity] 段及本机覆盖配置。' >&2
    exit 1
fi
if [[ $(prop ConnectivityCheckEnabled) == 'b false' ]]; then
    echo '正在启用连通性检测。可在 GNOME 隐私设置中关闭。'
    busctl --system set-property "$nm" "$obj" "$nm" ConnectivityCheckEnabled b true
fi
[[ $(prop ConnectivityCheckEnabled) == 'b true' ]] || { echo '开关未生效，请检查有效配置的 interval 和 enabled。'; exit 1; }
after=$(nmcli --wait 45 networking connectivity check)
echo "重新探测结果：$after"
case "$after" in
    full) exit 0;;
    portal) echo '网络返回了门户认证，请先登录该 Wi-Fi。';;
    *) echo '尚未验证公网连通，请核对 DNS、默认路由及探测地址在此 Wi-Fi 上的实际响应。';;
esac
exit 1
