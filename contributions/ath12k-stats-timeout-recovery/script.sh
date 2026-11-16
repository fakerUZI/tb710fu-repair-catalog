#!/usr/bin/env bash
# TB710FU: recover a verified ath12k firmware-statistics timeout symptom.
# This resets driver runtime state; it is not a permanent kernel fix.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C TZ=UTC
err() { printf '错误：%s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
[[ $EUID -eq 0 ]] || err '需要管理员权限。'
[[ $(uname -m) == aarch64 && $(uname -r) == 7.2.6-tb710fu* ]] || err '仅适用于 7.2.6-tb710fu 系列 ARM64 内核。'
[[ -r /sys/firmware/devicetree/base/model ]] || err '无法核对设备型号。'
model=$(tr -d '\000' < /sys/firmware/devicetree/base/model)
[[ $model == *TB710FU* ]] || err '设备型号不是 TB710FU。'
for c in journalctl nmcli ip iw timeout rmmod modprobe flock date awk grep tr mkdir stat cat sleep; do
  command -v "$c" >/dev/null || err "缺少依赖：$c"
done
act=${TB710FU_FIX_ACTION:-${TB710FU_WIFI_ACTION:-check}}
case "$act" in
  check) ;;
  apply|repair) act=repair ;;
  *) err '本临时恢复支持 check 或 apply（旧接口 repair）；没有持久安装可回退。' ;;
esac
state_dir=/run/tb710fu-wifi-stats-recovery
[[ ! -L $state_dir ]] || err '运行状态目录不能是符号链接。'
if [[ -e $state_dir ]]; then
  [[ -d $state_dir && $(stat -c %u "$state_dir") == 0 && $(stat -c %a "$state_dir") == 700 ]] || err '运行状态目录所有权不安全。'
fi
if [[ $act == repair ]]; then
  umask 077
  mkdir -p "$state_dir"
  [[ -d $state_dir && ! -L $state_dir && $(stat -c %u "$state_dir") == 0 && $(stat -c %a "$state_dir") == 700 ]] || err '运行状态目录所有权不安全。'
  [[ ! -L $state_dir/lock ]] || err '锁文件不能是符号链接。'
  exec 9>"$state_dir/lock"
  flock -n 9 || err '另一个 Wi-Fi 恢复实例正在运行。'
fi
[[ -d /sys/module/ath12k && -d /sys/module/ath12k_wifi7 ]] || err '未找到运行中的目标 ath12k 和 ath12k_wifi7 模块。'
mapfile -t ifaces < <(nmcli --terse --fields DEVICE,TYPE,STATE device status | awk -F: '$2=="wifi" && $3=="connected" {print $1}')
[[ ${#ifaces[@]} -eq 1 ]] || err '要求恰有一个已连接的 Wi-Fi 设备；未进行修改。'
iface=${ifaces[0]}
[[ $iface =~ ^[[:alnum:]_.-]+$ && -d /sys/class/net/$iface ]] || err '无线接口名称无效。'
[[ -r /sys/module/ath12k/parameters/tb_poll_off && -r /sys/module/ath12k_wifi7/parameters/tb_hw_dedup ]] || err '缺少本机适配的 Wi-Fi 模块参数；未进行修改。'
poll_before=$(cat /sys/module/ath12k/parameters/tb_poll_off)
dedup_before=$(cat /sys/module/ath12k_wifi7/parameters/tb_hw_dedup)
[[ $poll_before == 1 && $dedup_before == 1 ]] || err '已验证的轮询关闭及去重设置不匹配；未进行修改。'
now=$(date +%s); since=$((now-90)); stamp=$state_dir/last
if [[ -f $stamp && ! -L $stamp ]]; then
  read -r previous < "$stamp" || previous=0
  [[ $previous =~ ^[0-9]+$ ]] || err '上次执行记录无效。'
  if ((previous > since && previous <= now)); then since=$previous; fi
elif [[ -e $stamp || -L $stamp ]]; then
  err '上次执行记录不是普通文件。'
fi
start=$(date -u -d "@$since" '+%Y-%m-%d %H:%M:%S UTC')
if ! logs=$(journalctl -k -b --since "$start" --no-pager 2>/dev/null); then
  err '无法读取当前启动的内核日志，未进行修改。'
fi
count=$(printf '%s\n' "$logs" | awk 'index($0,"time out while waiting for fw stats done"){n++} END{print n+0}')
printf 'Wi-Fi 已连接；最近观察窗口内统计超时：%s 次。\n' "$count"
if [[ $act == check ]]; then
  if ((count >= 2)); then info '检测到反复超时；可在阅读风险后执行 repair。'; else info '未检测到连续超时。'; fi
  exit 0
fi
if ((count < 2)); then info '没有足够证据需要重置；保持现有连接。'; exit 0; fi
# Require a usable USB-NCM rescue interface for every reset. sudo may strip
# SSH_CONNECTION, so the environment alone is not a reliable safety check.
[[ -d /sys/class/net/usb0 && $(cat /sys/class/net/usb0/operstate) == up ]] || err '未检测到活跃 USB 网络，拒绝断开 Wi-Fi。'
ip -4 -o addr show dev usb0 | grep -q 'inet 172\.16\.42\.1/' || err '未检测到已知 USB 救援地址，拒绝断开 Wi-Fi。'
if [[ -n ${SSH_CONNECTION:-} ]]; then
  local_addr=${SSH_CONNECTION#* * }; local_addr=${local_addr%% *}
  [[ $local_addr == 172.16.42.1 ]] || err '当前 SSH 不通过 USB 救援地址；请从 USB 或平板本机执行。'
fi
info '正在重置 Wi-Fi 驱动；无线网络和远程桌面会暂时断开。'
# Re-enable the initially loaded device module if a later operation fails or
# receives a catchable signal. This cannot recover from power loss/panic.
restore() {
  rc=$?
  trap - EXIT INT TERM
  if [[ ! -d /sys/module/ath12k_wifi7 ]]; then
    if ! modprobe ath12k_wifi7; then printf '警告：自动恢复 Wi-Fi 驱动失败，请检查模块和网络。\n' >&2; rc=1; fi
  fi
  exit "$rc"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rmmod ath12k_wifi7 || err '无法卸载 Wi-Fi 设备模块。'
rmmod ath12k || err '无法卸载核心模块，正在恢复设备模块。'
modprobe ath12k_wifi7 || err '无法加载 Wi-Fi 模块，正在再次尝试恢复。'
[[ -d /sys/module/ath12k && -d /sys/module/ath12k_wifi7 ]] || err '驱动重新加载后未出现。'
[[ $(cat /sys/module/ath12k/parameters/tb_poll_off) == "$poll_before" && $(cat /sys/module/ath12k_wifi7/parameters/tb_hw_dedup) == "$dedup_before" ]] || err '驱动参数变化；请检查 modprobe 配置。'
for ((i=0;i<40;i++)); do
  if nmcli --terse --fields DEVICE,TYPE,STATE device status | awk -F: -v d="$iface" '$1==d && $2=="wifi" && $3=="connected"{ok=1} END{exit !ok}'; then break; fi
  sleep 1
done
nmcli --terse --fields DEVICE,TYPE,STATE device status | awk -F: -v d="$iface" '$1==d && $2=="wifi" && $3=="connected"{ok=1} END{exit !ok}' || err '驱动已加载，但 40 秒内没有恢复 Wi-Fi 连接。'
# Log a successful reset only after reconnection and parameter verification.
umask 077
printf '%s\n' "$(date +%s)" > "$stamp"
trap - EXIT INT TERM
if timeout 8 iw dev "$iface" station dump 2>/dev/null | grep -q 'signal:'; then
  info '驱动已重载、Wi-Fi 已重新连接，信号统计查询返回了结果。'
else
  info '驱动已重载且 Wi-Fi 已重新连接；未能验证信号统计，请检查日志。'
  exit 1
fi
info '此操作只清除当前异常状态，不能保证超时不会再次出现。'
