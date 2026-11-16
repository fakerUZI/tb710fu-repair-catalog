#!/bin/bash
# TB710FU 触摸窗口抓取：手指拖动 / 缩放宽边框为无边框的 X11 窗口（Electron 等）
# 背景：Chromium 的 -webkit-app-region: drag 与窗口缩放热区只认鼠标事件，
#       平板上没有鼠标，于是窗口既拖不动也缩不了。本工具用 XInput2 被动触摸
#       抓取，在按下瞬间分流：角落缩放 / 边缘单轴缩放 / 顶部条拖动 / 其余交还应用。
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "✗ 请用普通桌面用户运行，不要 sudo / root（本包全部是用户级改动）" >&2
    exit 1
fi

AT="${TB710FU_ATTACHMENTS_DIR:-}"
[ -n "$AT" ] && [ -d "$AT" ] || { echo "✗ 缺少附件目录（TB710FU_ATTACHMENTS_DIR）" >&2; exit 1; }

BIN="$HOME/.local/bin"
CFG="$HOME/.config/touch-window-grip"
UD="$HOME/.config/systemd/user"

echo "== 0. 检查前提 =="
[ -n "${DISPLAY:-}" ] || echo "  ! 当前 shell 没有 DISPLAY，本工具只对 XWayland/X11 窗口有效"
command -v python3 >/dev/null || { echo "✗ 缺少 python3" >&2; exit 1; }
echo "  ✓ python3 $(python3 -V 2>&1 | awk '{print $2}')"

echo "== 1. 安装程序与配置 =="
mkdir -p "$BIN" "$CFG" "$UD"
install -m 0755 "$AT/touch-window-grip" "$BIN/touch-window-grip"
if [ -e "$CFG/targets.json" ]; then
    cp -a "$CFG/targets.json" "$CFG/targets.json.bak-$(date +%Y%m%d-%H%M%S)"
    echo "  · 已备份原有 targets.json，保留你的目标配置不覆盖"
else
    install -m 0644 "$AT/targets.json" "$CFG/targets.json"
    echo "  ✓ 写入默认 targets.json"
fi

echo "== 2. 安装常驻服务 =="
install -m 0644 "$AT/touch-window-grip.service" "$UD/touch-window-grip.service"
systemctl --user daemon-reload
systemctl --user enable --now touch-window-grip.service 2>/dev/null || systemctl --user restart touch-window-grip.service
sleep 2
echo "  状态: $(systemctl --user is-active touch-window-grip.service 2>/dev/null || echo 未知)"

echo "== 3. 自检（无窗口时会跳过）=="
python3 "$BIN/touch-window-grip" --status 2>&1 | sed 's/^/  /' || true

cat <<'EOS'

== 完成 ==
使用：
  · 目前默认只接管 targets.json 里 match 命中的窗口（出厂只有 workbuddy）。
  · 增加一个应用：touch-window-grip --add firefox   （或用窗口标题关键字）
  · 查看清单：touch-window-grip --list
  · 移除一个：touch-window-grip --remove firefox
手感调节（~/.config/touch-window-grip/targets.json 的 defaults）：
  corner 54  角落缩放区边长（px）
  edge   18  边缘单轴缩放区宽度（px）
  strip  80  顶部拖动条高度（px）
  tap reject 抓取判定为非拖动/缩放时，原样交还给应用（保证点击与滚动不被抢）
回滚：
  systemctl --user disable --now touch-window-grip.service
  rm -f ~/.local/bin/touch-window-grip
注意：
  · 只对 XWayland / X11 窗口有效，原生 Wayland 客户端抓不到。
  · 日志：journalctl --user -u touch-window-grip -f
EOS
