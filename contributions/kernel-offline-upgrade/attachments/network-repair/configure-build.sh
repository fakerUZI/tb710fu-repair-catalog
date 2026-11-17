#!/bin/bash
# Source/apply in a matching kernel tree before its full modules/Image build.
set -euo pipefail
fix_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f Makefile && -x scripts/config && -f .config ]]
for name in 0001-ath12k-vdev-restart-count.patch 0002-ath12k-station-start-once.patch 0003-ath12k-identify-vdev-fix.patch; do
    patch --dry-run --batch --forward -p1 -F0 < "$fix_dir/$name"
    patch --batch --forward -p1 -F0 < "$fix_dir/$name"
done
while IFS= read -r line; do
    [[ $line == CONFIG_*=* ]] || continue
    symbol=${line%%=*}; symbol=${symbol#CONFIG_}; value=${line#*=}
    case $value in
        y) scripts/config --enable "$symbol" ;;
        m) scripts/config --module "$symbol" ;;
        *) echo "Unsupported config line: $line" >&2; exit 1 ;;
    esac
done < "$fix_dir/netfilter.config"
make -s LOCALVERSION= olddefconfig
while IFS= read -r line; do
    [[ $line == CONFIG_*=* ]] || continue
    grep -Fxq "$line" .config || { echo "Dependency disabled requested option: $line" >&2; exit 1; }
done < "$fix_dir/netfilter.config"
