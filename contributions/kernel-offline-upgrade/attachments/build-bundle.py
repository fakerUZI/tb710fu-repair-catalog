#!/usr/bin/env python3
"""Assemble a new dated bundle; private front modules and device inventories excluded."""
import argparse,hashlib,importlib.util,json,os,shutil,tarfile
from pathlib import Path


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--inventory',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    os.umask(0o077);assert not a.output.exists() and not a.output.with_suffix('.tar.gz').exists()
    source=Path(__file__).resolve().parent;project=a.project.resolve(strict=True)
    artifacts=project/'artifacts/kernel-7.2.6-tb710fu-20261117';built=artifacts/'build-output'
    spec=importlib.util.spec_from_file_location('dated_upgrade',source/'upgrade-ubuntu-kernel.py');u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
    inventory=json.loads(a.inventory.read_text())
    proof=json.loads((artifacts/'boot-dated-CANDIDATE.proof.json').read_text())
    assert proof['source_boot_sha256']==inventory['boot_sha256'] and proof['version']==u.VERSION
    assert proof['linux_sha256']==sha(built/'Image') and proof['boot_sha256']==sha(artifacts/'boot-dated-CANDIDATE.img')
    assert len(inventory['gpt'])==6
    a.output.mkdir(parents=True)
    copies={
        'boot.img':artifacts/'boot-dated-CANDIDATE.img',
        'modules.tar':built/('modules-'+u.VERSION+'.tar'),
        'kernel.config':built/'config',
        'tb-camera-live.sh':artifacts/'tb-camera-live.sh',
        'tb-camera-focus.sh':artifacts/'tb-camera-focus.sh',
        'tb-front-camera.sh':artifacts/'tb-front-camera.sh',
        'tb-ubuntu-usb-ncm':artifacts/'tb-ubuntu-usb-ncm',
        'tb-usb-ncm-identity.py':artifacts/'tb-usb-ncm-identity.py',
        'kernel-release.py':source/'kernel-release.py',
        'kernel-release.json':source/'kernel-release.json',
        'repair-ubuntu-snap.py':project/'offline-prep/snap-support/repair-ubuntu-snap.py',
        '91-tb710fu-camera-manual.conf':project/'offline-prep/snap-support/91-tb710fu-camera-manual.conf',
        'verify-netfilter.py':project/'offline-prep/network-repair/verify-netfilter.py',
    }
    for name in u.HELPERS+('ov13b10-rggb-UNTESTED.ko','qcom-camss-graph-UNTESTED.ko'):copies[name]=built/'rear-helpers'/name
    for name,path in copies.items():shutil.copyfile(path,a.output/name);(a.output/name).chmod(0o644)
    (a.output/'90-tb710fu-netfilter.conf').write_text('# Dated kernel: formal IPv4/IPv6 legacy tables and xt NAT targets.\nip_tables\niptable_filter\niptable_nat\nip6_tables\nip6table_filter\nip6table_nat\nxt_nat\nxt_REDIRECT\nxt_NETMAP\nxt_MASQUERADE\n')
    (a.output/'UPGRADE-USAGE-zh.md').write_text('TB710FU 日期内核 '+u.VERSION+'\n\n本次接受所有不带日期的合法旧内核，仍须通过当前固件/GPT、boot 和配套文件匹配。默认通过目录入口 check，只读预检。install 备份并写 boot_a，不自动重启；升级成功后手动重启，自动收尾验证 Snap strict/full。status/finish/rollback 使用同一目录入口；回退恢复本机备份后手动重启。\n\n前后摄：保留后摄和对焦适配。前摄若已启用，必须事先准备匹配新版本的本机私有模块目录 /opt/tb710fu-front-camera/'+u.VERSION+'；包内仅含通用启动脚本，不包含私人表数据或私有前摄模块，缺少配套时在写入前拒绝。\n\n本机备份保存在 /var/backups/tb710fu-kernel-upgrade/'+u.VERSION+'，不应公开上传。原始 boot/全部 GPT/文件备份与状态均保留；回退仍核实当前固件/GPT及文件，备份损坏或其他修改会拒绝覆盖。完整无法启动时需外部救援使用本机备份。\n')
    previous={}
    for target,name in (u.FILES|u.OPTIONAL_FILES).items():
        if target in inventory['files']:previous[target]=[inventory['files'][target]['sha256'],sha(a.output/name)]
    old=project/'artifacts/snap-support-20261116'
    for target,name in [('/opt/tb710fu-camera-test/tb-camera-live.sh','camera-live-original.sh'),('/opt/tb710fu-camera-test/tb-camera-focus.sh','camera-focus-original.sh'),('/usr/local/sbin/tb-ubuntu-usb-ncm','usb-ncm-original.sh')]:
        if (old/name).exists():previous[target].append(sha(old/name))
    manifest={'format':2,'version':u.VERSION,'release':u.RELEASE_PLAN,
              'files':{p.name:sha(p) for p in sorted(a.output.iterdir())},
              'gpt_layout':u.normalized_gpt(inventory['gpt']),
              'firmware':inventory['firmware'],'previous_files':previous,
              'private_front_helpers':{p.name:sha(p) for p in sorted((built/'private-front').glob('*.ko'))},
              'required_firmware_files':{'/lib/firmware/tb710fu-camera-graph-UNTESTED.dtbo':inventory['files']['/lib/firmware/tb710fu-camera-graph-UNTESTED.dtbo']['sha256']},
              'private_front_overlay_sha256':inventory['files']['/lib/firmware/tb-front-raw.dtbo']['sha256']}
    assert set(manifest['private_front_helpers']) == {'gc13a2_raw.ko','tb_front_registration.ko'}
    (a.output/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    code=(source/'upgrade-ubuntu-kernel.py').read_text()
    for anchor,replacement in [("MANIFEST_SHA256 = 'BUILD_TIME_MANIFEST_SHA256'","MANIFEST_SHA256 = '"+sha(a.output/'manifest.json')+"'"),("BOOT_HASH = 'BUILD_TIME_BOOT_SHA256'","BOOT_HASH = '"+proof['boot_sha256']+"'")]:
        assert code.count(anchor)==1;code=code.replace(anchor,replacement)
    (a.output/'upgrade-ubuntu-kernel.py').write_text(code)
    (a.output/'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(a.output.iterdir())))
    archive=a.output.with_suffix('.tar.gz')
    with tarfile.open(archive,'w:gz',compresslevel=6) as ar:ar.add(a.output,arcname=a.output.name)
    print('Candidate bundle:',archive,'SHA256:',sha(archive))

if __name__=='__main__':main()
