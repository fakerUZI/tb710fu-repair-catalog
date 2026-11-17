#!/usr/bin/env python3
"""Read-only device inventory. Output is private and must never be published."""
import hashlib,json,os,platform,subprocess
from datetime import datetime,timezone
from pathlib import Path


def run(*args):
    return subprocess.check_output(args,text=True).strip()


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    assert os.geteuid()==0
    boot=Path('/dev/disk/by-partlabel/boot_a').resolve(strict=True)
    block=Path('/sys/class/block')/boot.name
    assert (block/'partition').is_file()
    disks={}
    for node in sorted(Path('/sys/class/block').glob('sd*')):
        if not (node/'partition').exists():disks['/dev/'+node.name]=json.loads(run('sfdisk','--json','/dev/'+node.name))
    names=('abl_a','abl_b','xbl_a','xbl_b','xbl_config_a','xbl_config_b','dtbo_a','vendor_boot_a')
    files={}
    for name in ('/opt/tb710fu-camera-test/tb-camera-live.sh','/opt/tb710fu-camera-test/tb-camera-focus.sh','/opt/tb710fu-front-camera/tb-front-camera.sh','/etc/systemd/system/tb710fu-front-camera.service','/usr/local/sbin/tb-ubuntu-usb-ncm','/etc/modprobe.d/91-tb710fu-camera-manual.conf','/lib/firmware/tb-front-raw.dtbo','/lib/firmware/tb710fu-camera-graph-UNTESTED.dtbo'):
        p=Path(name)
        if p.exists():files[name]={'sha256':digest(p),'size':p.stat().st_size}
    result={'captured_at':datetime.now(timezone.utc).isoformat(),'version':platform.release(),'os_release':Path('/etc/os-release').read_text(),'cmdline':Path('/proc/cmdline').read_text(),'boot':str(boot),'boot_size':int(run('blockdev','--getsize64',str(boot))),'boot_sha256':digest(boot),'boot_b_sha256':digest('/dev/disk/by-partlabel/boot_b'),'gpt':disks,'firmware':{n:digest('/dev/disk/by-partlabel/'+n) for n in names},'files':files,'holds':run('apt-mark','showhold')}
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
