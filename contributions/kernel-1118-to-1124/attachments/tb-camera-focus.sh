#!/bin/bash
set -euo pipefail
state=/var/lib/tb710fu-camera-test/focus-start-in-progress
module=/opt/tb710fu-camera-test/tb_focus_manual.ko
release=$(uname -r)
if [[ $release == 7.2.6-tb710fu-snap1 || $release == 7.2.6-tb710fu-20261117 || $release == 7.2.6-tb710fu-20261118 || $release == 7.2.6-tb710fu-20261124 ]]; then module=/opt/tb710fu-camera-test/$release/tb_focus_manual.ko; fi
sensor=/sys/firmware/devicetree/base/soc@0/cci@ac15000/i2c-bus@1/camera@36

case ${1:-} in
  start)
    [[ ( $(uname -r) == 7.2.6-tb710fu || $(uname -r) == 7.2.6-tb710fu-snap1 || $(uname -r) == 7.2.6-tb710fu-20261117 || $(uname -r) == 7.2.6-tb710fu-20261118 || $(uname -r) == 7.2.6-tb710fu-20261124 ) && ! -e $state && ! -d /sys/module/tb_focus_manual ]]
    client=''
    for p in /sys/bus/i2c/devices/*-0036; do
      [[ -e $p/of_node && $(readlink -f "$p/of_node") == "$sensor" ]] || continue
      [[ -z $client ]] || exit 1
      client=${p##*/}
    done
    [[ $client =~ ^([0-9]+)-0036$ ]] || exit 1
    bus=${BASH_REMATCH[1]}
    install -d -m 755 "${state%/*}"
    : > "$state"
    sync -f "$state"
    sync -f "${state%/*}"
    insmod "$module" "bus=$bus"
    if ! echo 700 > /sys/module/tb_focus_manual/parameters/position; then
      rmmod tb_focus_manual || true
      exit 1
    fi
    rm "$state"
    sync -f "${state%/*}"
    ;;
  stop)
    [[ ! -d /sys/module/tb_focus_manual ]] || rmmod tb_focus_manual
    ;;
  *) exit 2 ;;
esac
