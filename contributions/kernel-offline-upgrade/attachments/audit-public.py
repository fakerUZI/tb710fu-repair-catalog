#!/usr/bin/env python3
"""Audit selected publishable artifacts against fresh private identity inventory."""
import argparse,hashlib,json,re,tarfile,uuid
from pathlib import Path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',type=Path,required=True);ap.add_argument('--inventory',type=Path,required=True);a=ap.parse_args()
    i=json.loads(a.inventory.read_text());needles=[]
    for value in i['gpt'].values():
        table=value['partitiontable']
        for value in [table.get('id')]+[p.get('uuid') for p in table['partitions']]:
            if value:
                u=uuid.UUID(value);needles.extend([str(u).encode(),str(u).upper().encode(),u.bytes,u.bytes_le])
    manifest=json.loads((a.bundle/'manifest.json').read_text());allowed=set(manifest['files'])|{'manifest.json','upgrade-ubuntu-kernel.py','SHA256SUMS'}
    assert {p.name for p in a.bundle.iterdir()}==allowed
    secrets=[b'-----BEGIN OPENSSH PRIVATE KEY-----',b'-----BEGIN RSA PRIVATE KEY-----',b'-----BEGIN PRIVATE KEY-----',b'retired-front-camera',b'gc13a2_raw_init_regs']
    forbidden_names=('private','backup','gc13a2_raw','tb_front_registration','known_hosts','authorized_keys','id_ed25519','shadow')
    def scan(name,data):
        for needle in needles:
            assert needle not in data,'Private disk/partition identity in '+name
        for needle in secrets:
            assert needle not in data,(name,needle)
        assert not re.search(rb'/Users/[^/\x00\s]+/',data), 'Personal host path in '+name
    for p in a.bundle.iterdir():
        assert p.is_file() and not p.is_symlink();assert not any(n in p.name for n in forbidden_names)
        if p.name=='modules.tar':
            with tarfile.open(p) as ar:
                members=ar.getmembers()
                for m in members:
                    assert m.isfile() or m.isdir()
                    assert not any(n in Path(m.name).name for n in forbidden_names),m.name
                    if m.isfile():scan(m.name,ar.extractfile(m).read())
        else:scan(p.name,p.read_bytes())
    print('PASS: exact allowlist; no private front module/table source, backups, SSH keys, credential patterns, private disk/partition UUIDs (text/binary) or Mac paths.')

if __name__=='__main__':main()
