#!/bin/bash
# Add/remove the front path after the existing rear-camera service.
set -euo pipefail
base=/opt/tb710fu-front-camera
release=$(uname -r)
rear=/opt/tb710fu-camera-test/$release
if [[ $release == 7.2.6-tb710fu-20261117 || $release == 7.2.6-tb710fu-20261118 || $release == 7.2.6-tb710fu-20261124 ]]; then base=$base/$release; fi
state=/var/lib/tb710fu-front-camera
camss=$rear/qcom-camss-graph-UNTESTED.ko

remove_front() {
    # Refuse to remove any supplier while a capture client still holds it.
    [[ ! -d /sys/module/qcom_camss ]] || rmmod qcom_camss
    [[ ! -d /sys/module/gc13a2_raw ]] || rmmod gc13a2_raw
    [[ ! -d /sys/module/tb_front_registration ]] || rmmod tb_front_registration
    [[ -d /sys/module/qcom_camss ]] || insmod "$camss"
}

case "${1:-}" in
start)
    [[ $release == 7.2.6-tb710fu-snap1 || $release == 7.2.6-tb710fu-20261117 || $release == 7.2.6-tb710fu-20261118 || $release == 7.2.6-tb710fu-20261124 ]]
    [[ ! -d /sys/module/tb_front_registration && ! -d /sys/module/gc13a2_raw ]]
    [[ ! -e $state/start-in-progress ]]
    # Name retains baseline spelling; actual voltage must match this experiment.
    voltage=''
    for p in /sys/class/regulator/regulator.*/name; do
        if [[ $(cat "$p") == vreg_l14b_3p2 ]]; then
            voltage=$(cat "${p%/name}/microvolts")
        fi
    done
    [[ $voltage == 2800000 ]] || { echo "L14B must be 2800000uV; got $voltage" >&2; exit 1; }
    mkdir -p "$state"
    : > "$state/start-in-progress"
    sync -f "$state/start-in-progress"
    trap 'echo "Front startup failed; restoring rear graph" >&2; remove_front || true' ERR
    modprobe v4l2-cci
    rmmod qcom_camss
    insmod "$base/tb_front_registration.ko" overlay_name=tb-front-raw.dtbo
    insmod "$base/gc13a2_raw.ko"
    insmod "$camss"
    for i in {1..40}; do
        [[ -L /sys/bus/i2c/devices/4-0039/driver && -e /dev/media0 ]] && break
        sleep .25
    done
    [[ -L /sys/bus/i2c/devices/4-0039/driver ]]
    [[ -L /sys/bus/i2c/devices/5-0036/driver ]]
    [[ -L /sys/bus/platform/devices/acb6000.isp/driver ]]
    # CAMSS binds asynchronously; device links can exist before graph completion.
    ready=false
    for i in {1..20}; do
        timeout 5 cam -l > "$state/enumeration.log" 2>&1
        if grep -q "'gc13a2_raw'" "$state/enumeration.log" &&
           grep -q "'ov13b10'" "$state/enumeration.log"; then
            ready=true
            break
        fi
        sleep .25
    done
    "$ready"
    rm "$state/start-in-progress"
    sync -f "$state"
    trap - ERR
    echo 'Front GC13A2 and existing rear camera enumerated'
    ;;
stop) remove_front ;;
*) echo "Usage: $0 start|stop" >&2; exit 2 ;;
esac
