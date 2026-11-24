#!/bin/bash
set -euo pipefail

assets=${TB710FU_ATTACHMENTS_DIR:?请先通过修复助手下载附件。}
work=$(mktemp -d -t tb710fu-droidloom-pen.XXXXXXXX)
trap 'rm -f -- "$work/droidloom-wayland" "$work/droidloom-wayland-r42" "$work/pen-compat.py"; rmdir -- "$work"' EXIT

assemble() {
    local name=$1 expected=$2
    if ! cat -- "$assets/$name".gz.{001,002,003,004,005} | gzip -dc > "$work/$name" ||
        ! (cd -- "$work" && printf '%s  %s\n' "$expected" "$name" | sha256sum -c - >/dev/null); then
        printf '%s\n' '手写笔程序分片不完整或校验失败，请在修复助手重新下载。' >&2
        return 1
    fi
}

assemble droidloom-wayland 8202e635e20a72eee63402ca6b7c66729e5fb0033d97413fee8cf7a97816c994
assemble droidloom-wayland-r42 71ce122993ea375882bf786303a0ed25975f155db35e731d2d309f0937b4fde5
cp -- "$assets/pen-compat.py" "$work/pen-compat.py"
/usr/bin/python3 -IB "$work/pen-compat.py" "${TB710FU_FIX_ACTION:-apply}"
