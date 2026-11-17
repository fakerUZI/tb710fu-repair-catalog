#!/bin/bash
# Reviewed snap1 module-only IPv4/IPv6 NAT + ath12k repair. No boot writes.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
action=${TB710FU_FIX_ACTION:-check}
case $action in check|apply|rollback|verify) ;; *) echo 'Action must be check/apply/rollback/verify' >&2; exit 2;; esac
[[ $EUID == 0 ]] || { echo '需要管理员权限。' >&2; exit 1; }
attachments=${TB710FU_ATTACHMENTS_DIR:-$(dirname -- "${BASH_SOURCE[0]}")/attachments}
archive=$attachments/network-repair-snap1-v2.tar.gz
python3 - "$archive" "$action" <<'PY'
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

archive=Path(sys.argv[1]);action=sys.argv[2]
expected='12fb64b5b12e7c0b9b8c7ee117de960abd5862de322fc9daf7a3e32815440a18'
if archive.is_symlink() or not archive.is_file(): raise SystemExit('缺少已校验的离线附件')
with archive.open('rb') as f:
    if hashlib.file_digest(f,'sha256').hexdigest()!=expected: raise SystemExit('离线附件 SHA256 不匹配')
name='tb710fu-network-repair-snap1-20261117-v2'
with tempfile.TemporaryDirectory(prefix='tb-network-repair-') as temp:
    with tarfile.open(archive,'r:gz') as ar:
        members=ar.getmembers()
        if len(members)>128 or sum(m.size for m in members)>16*1024*1024: raise SystemExit('异常附件大小')
        for m in members:
            p=PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!=name:
                raise SystemExit('附件路径越界')
            if not (m.isfile() or m.isdir()): raise SystemExit('不允许链接或特殊文件')
        ar.extractall(temp,filter='data')
    base=Path(temp)/name
    subprocess.run([sys.executable,str(base/'install-network-repair.py'),
                    'check' if action=='verify' else action],check=True)
    if action=='verify':
        marker=Path('/sys/module/ath12k/parameters/tb_vdev_count_fix')
        if not marker.exists() or marker.read_text().strip()!='1':
            raise SystemExit('修复驱动尚未生效，请重启后验证')
        subprocess.run([sys.executable,str(base/'verify-netfilter.py')],check=True)
PY
