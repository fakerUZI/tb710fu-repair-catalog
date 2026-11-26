#!/bin/sh
set -eu
assistant=/usr/share/tb710fu-repair-assistant
if [ ! -f "$assistant/setup_flow.py" ]; then
    echo '请先点击「更新修复助手」，升级到 0.10.20 后打开「Ubuntu 一次安装」。' >&2
    exit 1
fi
exec /usr/bin/python3 -IBu "$assistant/one-click-upgrade.py" "${TB710FU_FIX_ACTION:-apply}"
