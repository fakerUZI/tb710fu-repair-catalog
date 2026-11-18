#!/usr/bin/python3
"""Load the reviewed flash add-on only on its exact kernel/firmware/GPT profile."""
import copy,gzip,hashlib,json,os,platform,re,subprocess,sys
from pathlib import Path

VERSION=os.uname().release if os.uname().release in ('7.2.6-tb710fu-20261117', '7.2.6-tb710fu-20261118') else '7.2.6-tb710fu-20261118'
ROOT=Path('/opt/tb710fu-flash')/VERSION
LED=Path('/sys/class/leds/tb710fu:flash:rear')


def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT).strip()
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def require(ok,text):
    if not ok:raise RuntimeError(text)


def normalized(tables):
    result=[]
    for wrapped in tables.values():
        table=copy.deepcopy(wrapped['partitiontable']);table.pop('device',None);table.pop('id',None)
        for p in table['partitions']:
            p.pop('node',None);p.pop('uuid',None)
            if p.get('name')=='boot_a':
                def strip(match):
                    bits=[int(x) for x in match[1].split(',') if int(x) not in (51,52,53,54)]
                    return 'GUID:'+','.join(map(str,bits)) if bits else ''
                p['attrs']=re.sub(r'GUID:([0-9,]+)',strip,p.get('attrs','')).strip()
        result.append(table)
    return sorted(result,key=lambda t:json.dumps(t,sort_keys=True))


def normalize_attrs(value, ignored_bits):
    def normalize_guid(match):
        bits = sorted(set(map(int, match[1].split(','))) - ignored_bits)
        return 'GUID:' + ','.join(map(str, bits)) if bits else ''
    return ' '.join(re.sub(r'\bGUID:([0-9]+(?:,[0-9]+)*)(?=\s|$)',
                           normalize_guid, value).split())


def normalized_gpt(tables, *, identity=False):
    """Retain geometry and safety flags. Allow documented A/B state to vary."""
    result = []
    for wrapped in tables.values():
        table = copy.deepcopy(wrapped['partitiontable'])
        table.pop('device', None)
        if not identity:
            table.pop('id', None)
        names = {part.get('name', '') for part in table['partitions']}
        for partition in table['partitions']:
            partition.pop('node', None)
            if not identity:
                partition.pop('uuid', None)
            name = partition.get('name', '')
            ignored = set()
            if name in {'boot_a', 'boot_b'}:
                ignored = {48, 49, 51, 52, 53, 54}
            elif name.endswith(('_a', '_b')) and name[:-1] + ('b' if name.endswith('_a') else 'a') in names:
                ignored = {48, 49, 51, 52, 53, 54}
            attrs = normalize_attrs(partition.pop('attrs', ''), ignored)
            if attrs:
                partition['attrs'] = attrs
        result.append(table)
    return sorted(result, key=lambda x: json.dumps(x, sort_keys=True))


def preflight(installed=True):
    require(os.geteuid()==0,'需要管理员权限')
    require(platform.machine()=='aarch64' and platform.release()==VERSION,'仅支持已审核的 20261117 / 20261118 ARM64 内核')
    require(b'lenovo,tb710fu' in Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0'),'机型不匹配')
    m=json.loads((ROOT/'manifest.json').read_text())
    require(hashlib.sha256(gzip.decompress(Path('/proc/config.gz').read_bytes())).hexdigest()==m['config_sha256'],'运行配置不匹配')
    require(sha('/dev/disk/by-partlabel/boot_a')==m['boot_sha256'],'当前 boot 不匹配')
    tables={}
    for p in sorted(Path('/sys/class/block').glob('sd*')):
        if (p/'partition').exists():continue
        node='/dev/'+p.name
        require('No problems found.' in run(['sgdisk','-v',node]),'GPT 校验失败：'+node)
        tables[node]=json.loads(run(['sfdisk','--json',node]))
    require((normalized(tables) if VERSION=='7.2.6-tb710fu-20261117' else normalized_gpt(tables))==m['gpt_layout'],'当前 GPT 布局不匹配')
    for name,expected in m['firmware'].items():
        require(sha('/dev/disk/by-partlabel/'+name)==expected,'固件不匹配：'+name)
    for name,expected in m['files'].items():
        require(Path(name).name==name,'模块清单路径无效')
        p=ROOT/name
        require(p.is_file() and not p.is_symlink() and sha(p)==expected,'配套文件校验失败：'+name)
        if p.suffix=='.ko':require(run(['modinfo','-F','vermagic',str(p)]).split()[0]==VERSION,'模块版本不匹配')
    if installed:require(sha('/lib/firmware/tb-flash.dtbo')==m['files']['tb-flash.dtbo'],'动态覆盖文件校验失败')


def start():
    preflight()
    if Path('/sys/module/tb_flash_overlay').exists():
        require((Path('/sys/module/leds_qcom_flash/parameters/tb_flash_guard').read_text().strip())=='1','已加载的 flash 驱动不匹配')
        require(LED.exists(),'flash 节点未绑定')
        (LED/'brightness').write_text('0')
        return
    require(not Path('/sys/module/leds_qcom_flash').exists(),'存在其他 flash 驱动，请先检查')
    for name in ('led-class-flash','v4l2-async','videodev'):subprocess.run(['modprobe',name],check=True)
    loaded=[]
    try:
        for filename,name in [('v4l2-flash-led-class.ko','v4l2_flash_led_class'),('leds-qcom-flash.ko','leds_qcom_flash'),('tb_flash_overlay.ko','tb_flash_overlay')]:
            subprocess.run(['insmod',str(ROOT/filename)],check=True);loaded.append(name)
        require(LED.exists(),'驱动没有创建预期 LED 节点')
        (LED/'brightness').write_text('0')
    except BaseException:
        for name in reversed(loaded):subprocess.run(['rmmod',name],check=False)
        raise


def stop():
    if LED.exists():(LED/'brightness').write_text('0')
    for name in ('tb_flash_overlay','leds_qcom_flash','v4l2_flash_led_class'):
        if Path('/sys/module',name).exists():subprocess.run(['rmmod',name],check=True)

if __name__=='__main__':
    try:
        action=sys.argv[1] if len(sys.argv)>1 else 'check'
        {'check':preflight,'start':start,'stop':stop}[action]()
    except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('停止：'+str(exc))
