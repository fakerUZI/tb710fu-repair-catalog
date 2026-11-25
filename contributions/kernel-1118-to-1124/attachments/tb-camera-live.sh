#!/bin/bash
# Experimental TB710FU camera graph. Does not flash or modify the boot chain.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
base=/opt/tb710fu-camera-test
release=$(uname -r)
if [[ $release == 7.2.6-tb710fu-snap1 || $release == 7.2.6-tb710fu-20261117 || $release == 7.2.6-tb710fu-20261118 || $release == 7.2.6-tb710fu-20261124 ]]; then base=$base/$release; fi
state=/var/lib/tb710fu-camera-test
run=/run/tb710fu-camera-test
overlay=tb710fu-camera-graph-UNTESTED.dtbo
sensor_path=/sys/firmware/devicetree/base/soc@0/cci@ac15000/i2c-bus@1/camera@36

stop_owned() {
    # Do not remove suppliers while a downstream module is still in use.
    if [[ -f $run/camss ]]; then rmmod qcom_camss; rm -f "$run/camss"; fi
    if [[ -f $run/sensor ]]; then rmmod ov13b10; rm -f "$run/sensor"; fi
    if [[ -f $run/camss_alias ]]; then rmmod tb_camss_alias; rm -f "$run/camss_alias"; fi
    if [[ -f $run/sensor_alias ]]; then rmmod tb_camera_alias; rm -f "$run/sensor_alias"; fi
    if [[ -f $run/overlay ]]; then rmmod tb_camera_overlay; rm -f "$run/overlay"; fi
}

start() {
    [[ $(uname -r) == 7.2.6-tb710fu || $(uname -r) == 7.2.6-tb710fu-snap1 || $(uname -r) == 7.2.6-tb710fu-20261117 || $(uname -r) == 7.2.6-tb710fu-20261118 || $(uname -r) == 7.2.6-tb710fu-20261124 ]] || { echo 'Unsupported kernel' >&2; exit 1; }
    mkdir -p "$state" "$run"
    [[ ! -e $state/start-in-progress ]] || { echo 'Previous startup incomplete; refusing retry' >&2; exit 1; }
    for name in tb_camera_overlay tb_camera_alias tb_camss_alias ov13b10 qcom_camss; do
        [[ ! -d /sys/module/$name ]] || { echo "Module $name already loaded" >&2; exit 1; }
    done
    [[ $(tr -d '\0' </sys/firmware/devicetree/base/soc@0/cci@ac15000/status) == disabled ]]
    [[ $(tr -d '\0' </sys/firmware/devicetree/base/soc@0/isp@acb6000/status) == disabled ]]
    python3 - <<'PY'
from pathlib import Path
import struct
base = Path('/sys/firmware/devicetree/base/soc@0')
for node, expected in [('pinctrl@f100000', 0x75), ('clock-controller@ade0000', 0xe1)]:
    assert struct.unpack('>I', (base / node / 'phandle').read_bytes())[0] == expected
PY
    : > "$state/start-in-progress"
    sync -f "$state/start-in-progress"
    sync -f "$state"
    trap 'echo "Camera startup failed; attempting cleanup" >&2; stop_owned || true' ERR
    modprobe i2c_qcom_cci
    modprobe videobuf2_dma_sg
    modprobe v4l2_fwnode
    insmod "$base/tb_camera_overlay.ko" "overlay_name=$overlay"
    touch "$run/overlay"
    for _ in {1..40}; do
        [[ -e /sys/bus/platform/devices/acb6000.isp ]] && break
        sleep .25
    done
    local client='' p
    for p in /sys/bus/i2c/devices/*-0036; do
        [[ -e $p/of_node ]] || continue
        if [[ $(readlink -f "$p/of_node") == "$sensor_path" ]]; then
            [[ -z $client ]] || { echo 'Ambiguous camera sensor' >&2; return 1; }
            client=${p##*/}
        fi
    done
    [[ -n $client && -e /sys/bus/platform/devices/acb6000.isp && ! -L /sys/bus/platform/devices/acb6000.isp/driver ]]
    insmod "$base/tb_camera_alias.ko" "client_name=$client"
    touch "$run/sensor_alias"
    insmod "$base/tb_camss_alias.ko"
    touch "$run/camss_alias"
    insmod "$base/ov13b10-rggb-UNTESTED.ko"
    touch "$run/sensor"
    [[ -L /sys/bus/i2c/devices/$client/driver ]] || { echo 'Sensor probe failed' >&2; return 1; }
    insmod "$base/qcom-camss-graph-UNTESTED.ko"
    touch "$run/camss"
    [[ -L /sys/bus/platform/devices/acb6000.isp/driver ]] || { echo 'CAMSS probe failed' >&2; return 1; }
    udevadm settle --timeout=10 || true
    [[ -e /dev/media0 ]] || { echo 'Camera media node absent' >&2; return 1; }
    rm "$state/start-in-progress"
    sync -f "$state"
    trap - ERR
    echo "TB710FU camera test graph active ($client)"
}

case "${1:-}" in
    start) start ;;
    stop) stop_owned ;;
    *) echo "Usage: $0 {start|stop}" >&2; exit 2 ;;
esac
