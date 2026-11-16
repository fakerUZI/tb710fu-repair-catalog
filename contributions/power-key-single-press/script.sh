#!/bin/bash
# TB710FU 电源键修正：熄屏后按一次即亮屏
# 原理：原 tb-gnome-power-button.py 用 Mutter.DisplayConfig.PowerSaveMode 判断
#       “屏幕是否已关”，但 GNOME 熄屏是 gsd-power 直接关 DRM 输出完成，该属性
#       长期为 0，于是熄屏后的第一次按键被当成“关屏”丢掉，必须按第二次。
# 本包改用真实判据：/sys/class/drm/*-DSI-1/dpms 与背光 bl_power。
# 做法：不改只读的系统文件，用用户级同名 unit 覆盖 /etc/systemd/user 里的版本。
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "✗ 请用普通桌面用户运行，不要 sudo / root（本包全部是用户级改动）" >&2
    exit 1
fi

AT="${TB710FU_ATTACHMENTS_DIR:-}"
[ -n "$AT" ] && [ -d "$AT" ] || { echo "✗ 缺少附件目录（TB710FU_ATTACHMENTS_DIR）" >&2; exit 1; }

BIN="$HOME/.local/bin"
UD="$HOME/.config/systemd/user"
WANTS="$UD/graphical-session.target.wants"

echo "== 0. 检查前提 =="
if [ ! -e /sys/class/drm/card1-DSI-1/dpms ] && ! ls /sys/class/drm/*-DSI-1/dpms >/dev/null 2>&1; then
    echo "✗ 找不到 DSI 输出的 dpms 节点，本机可能不是 TB710FU，已中止" >&2
    exit 1
fi
ls /sys/class/drm/*-DSI-1/dpms >/dev/null 2>&1 && echo "  ✓ dpms: $(ls /sys/class/drm/*-DSI-1/dpms | head -1)"
[ -d /sys/class/backlight/sy7758-backlight ] && echo "  ✓ 背光: sy7758-backlight" || echo "  ! 未找到 sy7758-backlight，脚本会退化为只看 dpms"

echo "== 1. 安装脚本 =="
mkdir -p "$BIN" "$UD" "$WANTS"
install -m 0755 "$AT/tb-power-key.py"  "$BIN/tb-power-key.py"
install -m 0755 "$AT/tb-power-key-off" "$BIN/tb-power-key-off"
echo "  ✓ $BIN/tb-power-key.py"

echo "== 2. 安装用户级 unit（覆盖系统同名单元，无需 root）=="
if [ -e "$UD/tb-gnome-power-button.service" ]; then
    cp -a "$UD/tb-gnome-power-button.service" "$UD/tb-gnome-power-button.service.bak-$(date +%Y%m%d-%H%M%S)"
    echo "  · 已备份原用户级 unit"
fi
install -m 0644 "$AT/tb-gnome-power-button.service" "$UD/tb-gnome-power-button.service"
ln -sf "$UD/tb-gnome-power-button.service" "$WANTS/tb-gnome-power-button.service"
echo "  ✓ $UD/tb-gnome-power-button.service"

echo "== 3. 启用并启动 =="
systemctl --user daemon-reload
systemctl --user enable --now tb-gnome-power-button.service 2>/dev/null \
    || systemctl --user restart tb-gnome-power-button.service

sleep 2
echo "== 4. 验证 =="
FRAG=$(systemctl --user show -p FragmentPath --value tb-gnome-power-button.service 2>/dev/null || true)
STATE=$(systemctl --user is-active tb-gnome-power-button.service 2>/dev/null || true)
echo "  单元文件: ${FRAG:-未知}"
echo "  运行状态: ${STATE:-未知}"
case "$FRAG" in
    "$HOME"*) echo "  ✓ 已由用户级覆盖接管";;
    *) echo "  ! FragmentPath 仍指向系统文件，检查 ~/.config/systemd/user 是否被 systemd 读到";;
esac
python3 - <<'PY' || true
import glob, pathlib
for p in sorted(glob.glob('/sys/class/drm/*-DSI-1/dpms')):
    print('  dpms =', pathlib.Path(p).read_text().strip(), '(', p, ')')
bl = pathlib.Path('/sys/class/backlight/sy7758-backlight/bl_power')
if bl.exists():
    print('  bl_power =', bl.read_text().strip())
PY

cat <<'EOS'

== 完成 ==
测试：合上保护套熄屏（或等自动熄屏）后按一次电源键，应立即亮屏。
回滚：tb-power-key-off          （移除本包，回到系统自带脚本）
说明：长按 1 秒仍然是关机请求，短按才是亮/熄屏切换。
EOS
