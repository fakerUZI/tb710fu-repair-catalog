#!/bin/sh
# Run in an Ubuntu arm64 build environment. The UI binary includes control-center.patch.
set -eu
src=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
build=$(realpath "${1:?build output directory}")
test -f "$build/gnome-control-center"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
prefix="$stage/usr/local/lib/tb710fu-face-unlock"
install -d "$prefix" "$stage/DEBIAN" "$stage/var/lib/tb710fu-face-password"
cc -std=c11 -Wall -Wextra -Werror -O2 -fPIC -shared -Wl,-z,relro,-z,now "$src/pam-face-password.c" -lpam -o "$prefix/pam-face-password.so"
install -m 0644 "$src/system-password.py" "$src/choose-mode.py" "$src/scan-notice.py" "$prefix/"
install -Dm644 "$src/tb710fu-face-password.desktop" "$stage/usr/share/applications/tb710fu-face-password.desktop"
install -m 0755 "$src/configure" "$prefix/"
install -Dm755 "$build/gnome-control-center" "$stage/usr/libexec/tb710fu-face-password/gnome-control-center"
install -Dm644 "$src/pam-profile" "$stage/usr/share/pam-configs/tb710fu-face-password"
install -Dm644 "$src/tb710fu-face-password.service" "$stage/usr/lib/systemd/system/tb710fu-face-password.service"
install -Dm644 "$src/org.tb710fu.face-password.policy" "$stage/usr/share/polkit-1/actions/org.tb710fu.face-password.policy"
cat > "$stage/DEBIAN/control" <<'CONTROL'
Package: tb710fu-face-password
Version: 1.0.3
Architecture: arm64
Maintainer: TB710FU local adaptations
Depends: libpam-runtime, libpam0g, python3-gi, pkexec, gir1.2-gtk-3.0
Description: Face verification at local GNOME and sudo password prompts for TB710FU
CONTROL
cat > "$stage/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -eu
if [ "$1" = configure ]; then
    test -r /usr/local/lib/tb710fu-face-unlock/face-unlock.py
    systemctl daemon-reload
    systemctl enable tb710fu-face-password.service
    systemctl restart tb710fu-face-password.service
    pam-auth-update --package
    grep -Fq '/pam-face-password.so' /etc/pam.d/common-auth || {
        echo '当前 PAM 配置有本地修改，请在 pam-auth-update 中选择 TB710FU 人脸验证。' >&2
        exit 1
    }
fi
SCRIPT
cat > "$stage/DEBIAN/prerm" <<'SCRIPT'
#!/bin/sh
set -eu
if [ "$1" = remove ] || [ "$1" = deconfigure ]; then
    pam-auth-update --package --remove tb710fu-face-password
    systemctl disable --now tb710fu-face-password.service
fi
SCRIPT
chmod 755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/prerm"
dpkg-deb --build --root-owner-group "$stage" "$build/tb710fu-face-password_1.0.3_arm64.deb"
