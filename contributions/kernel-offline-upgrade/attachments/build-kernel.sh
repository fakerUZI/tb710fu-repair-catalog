#!/bin/bash
# Offline build in tb-snap-kernel-build:20261116. No device access.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
plan=${TB710FU_KERNEL_RELEASE_PLAN:-$script_dir/kernel-release.json}
version=$(python3 "$script_dir/kernel-release.py" --plan "$plan" --target-only)
localversion=${version#7.2.6}
[[ $version == 7.2.6-tb710fu-* ]] || { echo 'This source builder only supports Linux 7.2.6' >&2; exit 1; }
project=/project
input=$project/artifacts/snap-support-20261116
out=$project/artifacts/kernel-$version/build-output
mkdir -p "$out"
cd /build
sha256sum "$project/artifacts/linux-7.2.6.tar.xz" | grep -q '^039aef84f2b0994aeda3f4fcfc3d02ec9d7a9bbb9020ea264c43f446c860f606 '
[ ! -d linux-7.2.6 ] || { echo 'Refusing to reuse a dirty source tree'; exit 1; }
tar -xf "$project/artifacts/linux-7.2.6.tar.xz"
cd linux-7.2.6
fk=$project/sources/armadaos-freedom/packages/kernel
: > "$out/patch-results.log"
while IFS= read -r name; do
 name=${name%%#*}; name=${name## }; name=${name%% *}; [ -n "$name" ] || continue
 if patch -p1 --batch --forward -F0 --no-backup-if-mismatch --quiet < "$fk/patches/$name" >> "$out/patch-results.log" 2>&1; then
  echo "OK $name"
 else
  echo "SKIPPED $name"
 fi
done < "$fk/patches/series"
cp "$fk"/dts/*.dts "$fk"/dts/*.dtsi arch/arm64/boot/dts/qcom/
for patchfile in "$fk"/dts/*.patch "$project/sources/tb710fu-hyperos-steamos/kernel/tb710fu-kernel-on-freedom.patch" "$project/sources/TB710FU-Archlinux/build/patches/0001-tb710fu-local.patch"; do
 patch -p1 --batch --forward -F0 --no-backup-if-mismatch < "$patchfile" >> "$out/patch-results.log" 2>&1 || echo "STALE HUNKS $patchfile"
done
# Match the existing port's Iris backports; report already-present hunks for review.
for patchfile in "$project"/sources/TB710FU-Archlinux/build/patches/000[2-6]-media-iris-*.patch; do
 patch -p1 --batch --forward -F0 --no-backup-if-mismatch < "$patchfile" >> "$out/patch-results.log" 2>&1 || echo "STALE IRIS HUNKS $patchfile"
done
patch -p1 --batch -F0 < "$project/offline-prep/snap-support/0001-apparmor-preserve-network-feature-abi.patch"
# Preserve the working rear-camera implementation only. No front-camera inputs.
cp "$input/rear-ov13b10.c" drivers/media/i2c/ov13b10.c
tar -xf "$input/rear-camss-source.tar" -C drivers/media/platform/qcom/camss
cp "$input/live.config" .config
scripts/config --set-str LOCALVERSION "$localversion" --disable LOCALVERSION_AUTO \
 --set-str INITRAMFS_SOURCE "$input/original-initramfs.cpio" \
 --enable SECURITY_APPARMOR --enable SECURITY_APPARMOR_INTROSPECT_POLICY \
 --enable SECURITY_APPARMOR_HASH --enable SECURITY_APPARMOR_HASH_DEFAULT \
 --enable SECURITY_APPARMOR_EXPORT_BINARY --enable SECURITY_APPARMOR_PARANOID_LOAD \
 --set-str LSM 'landlock,lockdown,yama,loadpin,safesetid,apparmor,ipe,bpf' \
 --enable SQUASHFS_XATTR --enable SQUASHFS_LZO --enable SQUASHFS_LZ4 \
 --module VIDEO_OV13B10 --module NETFILTER_XT_TARGET_MASQUERADE
bash "$(dirname -- "${BASH_SOURCE[0]}")/network-repair/configure-build.sh"
cp .config "$out/config"
make -j8 LOCALVERSION= Image modules > "$out/build.log" 2>&1
make -s LOCALVERSION= INSTALL_MOD_PATH=/build/modstage INSTALL_MOD_STRIP=1 modules_install >> "$out/build.log" 2>&1
[ "$(make -s LOCALVERSION= kernelrelease)" = "$version" ]
cp "$plan" "$out/kernel-release.json"
bash "$(dirname -- "${BASH_SOURCE[0]}")/network-repair/assert-modules.sh" "/build/modstage/lib/modules/$version"
# Rebuild only the four helpers used by the working rear camera and manual focus.
mkdir -p /build/rear-helpers
cp "$project"/offline-prep/camera-live-overlay/{tb_camera_overlay.c,tb_camera_alias.c,tb_camss_alias.c,tb_focus_manual.c} /build/rear-helpers/
printf 'obj-m += tb_camera_overlay.o tb_camera_alias.o tb_camss_alias.o tb_focus_manual.o\n' > /build/rear-helpers/Makefile
make -j4 LOCALVERSION= M=/build/rear-helpers modules >> "$out/build.log" 2>&1
mkdir -p "$out/rear-helpers"
cp /build/rear-helpers/*.ko "$out/rear-helpers/"
cp drivers/media/i2c/ov13b10.ko "$out/rear-helpers/ov13b10-rggb-UNTESTED.ko"
cp drivers/media/platform/qcom/camss/qcom-camss.ko "$out/rear-helpers/qcom-camss-graph-UNTESTED.ko"
rm -f /build/modstage/lib/modules/$version/{build,source}
tar -cf "$out/modules-$version.tar" -C /build/modstage/lib/modules "$version"
cp arch/arm64/boot/Image "$out/Image"
cp Module.symvers "$out/Module.symvers"
sha256sum "$out/Image" "$out/modules-$version.tar"
echo BUILD_DONE
