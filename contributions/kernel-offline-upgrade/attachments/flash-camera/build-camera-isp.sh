#!/bin/bash
# Disposable Ubuntu 26.04 ARM64 container only; never build on the tablet.
set -euo pipefail
export DEB_BUILD_PROFILES=nodoc DEBIAN_FRONTEND=noninteractive
cd /work
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
apt-get update
apt-get build-dep -y --no-install-recommends libcamera
apt-get install -y --no-install-recommends devscripts
apt-get source libcamera=0.7.0-1ubuntu2
echo 'ebd90a3aa2ca87a39323ffb7a4f5bbf72090b43a2431133759620b63e982db87  libcamera_0.7.0.orig.tar.gz' | sha256sum -c -
echo '0ee3195d74ad85b089c62eb7b2904023f8556544b2d9dbcc67aa834ddde5a09e  libcamera_0.7.0-1ubuntu2.debian.tar.xz' | sha256sum -c -
cd libcamera-0.7.0
for patch_file in /work/0003-*.patch /work/0004-*.patch /work/0005-*.patch /work/0006-*.patch; do
 cp "$patch_file" debian/patches/
 basename "$patch_file" >> debian/patches/series
 patch -p1 < "$patch_file"
done
DEBFULLNAME='TB710FU local build' DEBEMAIL=local@localhost \
 dch --newversion 0.7.0-1ubuntu2+tb710fu20261117.3 --distribution resolute \
 'Match TB710FU sensor gain units and correct software ISP unity threshold.'
DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -b -us -uc -j4
