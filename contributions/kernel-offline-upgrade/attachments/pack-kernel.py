#!/usr/bin/env python3
"""Replace Linux only in a freshly inventoried boot. Preserve DT, shim and Android."""
import argparse,hashlib,json,re,struct,subprocess,sys,tempfile
from pathlib import Path


def sha(b):return hashlib.sha256(b).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','inventory','linux','plan','output','avbtool','test-key'):
        ap.add_argument('--'+name,type=Path,required=True)
    a=ap.parse_args();assert not a.output.exists()
    inventory=json.loads(a.inventory.read_text());plan=json.loads(a.plan.read_text());version=plan['target_version']
    image=a.baseline.read_bytes();assert sha(image)==inventory['boot_sha256']
    assert len(image)==inventory['boot_size']==100663296 and image[:8]==b'ANDROID!'
    assert struct.unpack_from('<I',image,12)[0]==0
    size=struct.unpack_from('<I',image,8)[0];kernel=image[4096:4096+size]
    moff,msize=struct.unpack_from('<QQ',kernel,32)
    assert kernel[56:60]==b'ARM\x64' and moff+msize==len(kernel)
    manifest=bytearray(kernel[moff:]);assert struct.unpack_from('<IHHIII',manifest)==(0x464d504b,0,1,4,1,1000)
    entries=[];pos=60
    for _ in range(4):
        f=struct.unpack_from('<QQIIQIIIQI',manifest,pos)
        name=bytes(manifest[pos+56:pos+56+f[-1]]).decode()
        assert f[0]+f[1]<=moff
        entries.append((pos,f,name));pos=(pos+56+f[-1]+3)&~3
    assert pos==len(manifest)
    lp,lf,_=next(x for x in entries if x[2]=='Ubuntu' and x[1][2]==1)
    dp,df,_=next(x for x in entries if x[1][2]==5)
    assert lf[0]+lf[1]==df[0] and df[0]+df[1]<=moff
    for _,f,name in entries:
        if f!=lf and f!=df:assert f[0]+f[1]<=lf[0]
    dtb=kernel[df[0]:df[0]+df[1]];linux=a.linux.read_bytes()
    assert linux[56:60]==b'ARM\x64' and ('Linux version '+version+' ').encode() in linux
    doff=(lf[0]+len(linux)+7)&~7;new_moff=(doff+len(dtb)+3)&~3
    struct.pack_into('<Q',manifest,lp+8,len(linux));struct.pack_into('<Q',manifest,dp,doff)
    packed=bytearray(kernel[:lf[0]])+linux
    packed+=bytes(doff-len(packed))+dtb
    packed+=bytes(new_moff-len(packed))+manifest
    struct.pack_into('<Q',packed,16,len(packed));struct.pack_into('<Q',packed,32,new_moff)
    assert len(packed)<86*1024*1024,'ABL raw load limit exceeded'
    proof={'source_boot_sha256':sha(image),'version':version,'linux_sha256':sha(linux),'linux_size':len(linux),'packed_size':len(packed),'preserved':{}}
    for _,f,name in entries:
        if f==lf:continue
        start=doff if f==df else f[0]
        before=kernel[f[0]:f[0]+f[1]];after=packed[start:start+f[1]]
        assert before==after;proof['preserved'][name]=sha(after)
    header=bytearray(image[:4096]);struct.pack_into('<I',header,8,len(packed))
    body=header+packed+bytes((-len(packed))%4096)
    info=subprocess.check_output([sys.executable,str(a.avbtool),'info_image','--image',str(a.baseline)],text=True)
    salt=re.search(r'Salt:\s+([0-9a-f]+)',info)[1];rollback=re.search(r'Rollback Index:\s+(\d+)',info)[1]
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('xb') as output:
        output.write(body)
    cmd=[sys.executable,str(a.avbtool),'add_hash_footer','--image',str(a.output),'--partition_name','boot','--partition_size',str(len(image)),'--key',str(a.test_key),'--algorithm','SHA256_RSA4096','--rollback_index',rollback,'--salt',salt]
    for k,v in dict(re.findall(r"Prop: (\S+) -> '([^']*)'",info)).items():cmd+=['--prop',k+':'+v]
    subprocess.run(cmd,check=True)
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'boot.img';path.symlink_to(a.output.resolve())
        subprocess.run([sys.executable,str(a.avbtool),'verify_image','--image',str(path),'--key',str(a.test_key)],check=True)
    assert a.output.stat().st_size==len(image)
    proof['boot_sha256']=sha(a.output.read_bytes());a.output.with_suffix('.proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof,indent=2))

if __name__=='__main__':main()
