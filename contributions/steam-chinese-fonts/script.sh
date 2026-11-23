#!/usr/bin/env bash
# Make the existing Ubuntu Noto fonts available to the Steam Snap user runtime.
set -euo pipefail
shopt -s dotglob nullglob
export LC_ALL=C
umask 077

action=${TB710FU_FIX_ACTION:-check}
base=$HOME/snap/steam/common
font_dir=$base/.local/share/fonts
dest=$font_dir/tb710fu-cjk
manifest=.tb710fu-fonts.sha256
sources=(
    /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
    /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc
    /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf
)
fonts=("${sources[@]##*/}")

die() { printf '错误：%s\n' "$*" >&2; exit 1; }

case $action in
    check|apply|rollback) ;;
    *) die '操作必须为 check、apply 或 rollback。' ;;
esac
(( EUID != 0 )) || die '请用安装和运行 Steam 的普通用户执行。'
[[ $(uname -m) == aarch64 && -r /proc/device-tree/compatible ]] ||
    die '此修复仅适用于 ARM64 TB710FU。'
tr '\0' '\n' </proc/device-tree/compatible | grep -Fx 'lenovo,tb710fu' >/dev/null ||
    die '此修复仅适用于 TB710FU。'
[[ $base == /* ]] || die '需要绝对路径的 HOME。'

check_fonts() {
    local src
    for src in "${sources[@]}"; do
        [[ -f $src && -r $src ]] || die '请先安装 Ubuntu 的 fonts-noto-cjk 和 fonts-noto-color-emoji。'
    done
}

if [[ $action != rollback ]]; then
    grep -Eq '^ID=(ubuntu|"ubuntu")$' /etc/os-release &&
        grep -Eq '^VERSION_ID=(26\.04|"26\.04")$' /etc/os-release ||
        die '此修复已针对 Ubuntu 26.04 验证。'
    command -v snap >/dev/null && snap list steam >/dev/null 2>&1 ||
        die '请先安装 Steam Snap。'
    [[ -d $base/.local/share/Steam/steamrt64/pv-runtime/steam-runtime-steamrt ]] ||
        die '请先启动 Steam 并等首次客户端更新完成。'
    command -v fc-cache >/dev/null || die '请先安装 fontconfig。'
    check_fonts
fi

checksums() (
    cd -- "$1"
    sha256sum -- "${fonts[@]}"
)

verify_owned_directory() {
    [[ -d $dest && ! -L $dest ]] || die '字体路径不是普通目录，已保留原路径。'
    local entries=("$dest"/*) font i
    for font in "${fonts[@]}"; do
        [[ -f $dest/$font && ! -L $dest/$font ]] || die '字体目录内容与本修复不符。'
    done
    if [[ -e $dest/$manifest || -L $dest/$manifest ]]; then
        [[ -f $dest/$manifest && ! -L $dest/$manifest && ${#entries[@]} -eq 4 ]] ||
            die '字体目录含自定义文件，已保留原目录。'
        checksums "$dest" | cmp -s -- - "$dest/$manifest" || die '字体内容已改变，已保留原目录。'
    else
        [[ ${#entries[@]} -eq 3 ]] || die '字体目录含自定义文件，已保留原目录。'
        check_fonts
        for i in "${!fonts[@]}"; do
            cmp -s -- "${sources[i]}" "$dest/${fonts[i]}" ||
                die '未登记的字体与系统字体不同，已保留原目录。'
        done
    fi
}

if [[ $action == check ]]; then
    if [[ -e $dest || -L $dest ]]; then
        verify_owned_directory
        echo 'Steam 专用的中文和表情字体已就绪。'
    else
        echo 'Steam Snap 和系统字体检查通过，可以从修复助手单独运行此条目。'
    fi
    exit 0
fi

if [[ $action == rollback && ! -e $dest && ! -L $dest ]]; then
    echo '此修复的 Steam 专用字体目录不存在。'
    exit 0
fi

exec 9>"$base/.tb710fu-steam-fonts.lock"
flock -n 9 || die '已有另一个 Steam 字体操作在运行。'
if [[ -e $dest || -L $dest ]]; then
    verify_owned_directory
elif [[ $action == rollback ]]; then
    exit 0
fi

mkdir -p -- "$font_dir"
stage=$(mktemp -d -- "$base/.tb710fu-steam-fonts.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT

if [[ $action == rollback ]]; then
    mv -T -- "$dest" "$stage/removed"
    echo '已移除这项修复的专用字体。请完全退出并重新打开 Steam。'
    exit 0
fi

if [[ -d $dest ]]; then
    if [[ ! -f $dest/$manifest ]]; then
        checksums "$dest" >"$stage/$manifest"
        ln -T -- "$stage/$manifest" "$dest/$manifest"
    fi
    echo 'Steam 专用字体已安装。请完全退出并重新打开 Steam。'
    exit 0
fi

mkdir -m 755 -- "$stage/fonts"
install -m644 -- "${sources[@]}" "$stage/fonts/"
checksums "$stage/fonts" >"$stage/fonts/$manifest"
mv -T --no-clobber -- "$stage/fonts" "$dest"
[[ ! -d $stage/fonts ]] || die '目标目录已出现，已保留原目录。'
if ! XDG_DATA_HOME="$base/.local/share" XDG_CONFIG_HOME="$base/.config" \
     XDG_CACHE_HOME="$base/.cache" fc-cache -f "$dest"; then
    mv -T -- "$dest" "$stage/failed"
    die '刷新 Steam 字体缓存失败，已撤销本次字体安装。'
fi
echo '已补齐 Steam 的中文和表情字体。请完全退出并重新打开 Steam。'
