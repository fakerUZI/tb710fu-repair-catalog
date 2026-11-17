#!/bin/bash
set -euo pipefail
root=${1:?Pass the installed lib/modules/kernelrelease directory}
for name in ath12k ath12k_wifi7 x_tables nf_nat ip_tables iptable_filter iptable_mangle iptable_raw iptable_nat ipt_REJECT ip6_tables ip6table_filter ip6table_mangle ip6table_raw ip6table_nat ip6t_REJECT xt_nat xt_REDIRECT xt_NETMAP; do
    mapfile -t found < <(find "$root/kernel" -name "$name.ko" -type f)
    [[ ${#found[@]} == 1 ]] || { echo "Missing/duplicate network module: $name" >&2; exit 1; }
    [[ $(modinfo -F vermagic "${found[0]}") == "${root##*/} "* ]]
done
modinfo -F parm "$root/kernel/drivers/net/wireless/ath/ath12k/ath12k.ko" | grep -q '^tb_vdev_count_fix:'
