#!/usr/bin/env python3
"""Build the catalog bundle from verified public input files and a sanitized profile."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parts-dir', type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    source = here / 'upgrade-ubuntu-kernel.py'
    spec = importlib.util.spec_from_file_location('upgrader', source)
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    u.policy().validate(u.RELEASE_PLAN)
    assert json.loads((here/'kernel-release.json').read_text()) == u.RELEASE_PLAN
    profile = json.loads((here/'release-profile.json').read_text())
    assert profile.pop('known_source_boot_sha256') == sorted(u.OLD_HASHES)
    for table in profile['gpt_layout']:
        assert 'id' not in table and 'device' not in table
        assert all('node' not in part and 'uuid' not in part for part in table['partitions'])
    required = set((u.FILES | u.OPTIONAL_FILES | profile['camera_migration']).values())
    required.update(u.HELPERS)
    required.update({'boot.img','modules.tar','kernel.config','tb_dp_overlay.ko',
                     'ov13b10-rggb-UNTESTED.ko','qcom-camss-graph-UNTESTED.ko',
                     'repair-ubuntu-snap.py','verify-netfilter.py'})
    actual = {p.name for p in args.inputs.iterdir()}
    assert actual == required, (sorted(required-actual), sorted(actual-required))
    expected = json.loads((here/'release-inputs.json').read_text())
    assert set(expected) == required
    args.output.mkdir(mode=0o700)
    for name in sorted(required):
        p = args.inputs/name
        assert p.is_file() and not p.is_symlink() and sha(p) == expected[name], name
        shutil.copyfile(p, args.output/name)
    for name in ('kernel-release.py','kernel-release.json','KERNEL-README.md'):
        shutil.copyfile(here/name, args.output/name)
    assert (args.output/'boot.img').stat().st_size == u.BOOT_SIZE
    u.validate_archive(args.output/'modules.tar')
    files = {p.name:sha(p) for p in sorted(args.output.iterdir())}
    manifest = args.output/'manifest.json'
    write_json(manifest, dict(profile, version=u.VERSION, release=u.RELEASE_PLAN, files=files))
    dest_installer = args.output/source.name
    dest_installer.write_text(source.read_text().replace('BUILD_TIME_BOOT_SHA256',files['boot.img'])
                              .replace('BUILD_TIME_MANIFEST_SHA256',sha(manifest)))
    (args.output/'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(args.output.iterdir())))
    archive = args.output.with_suffix('.tar.gz')
    with archive.open('xb') as target, gzip.GzipFile(filename='',fileobj=target,mode='wb',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w',format=tarfile.PAX_FORMAT) as tf:
            for f in sorted(args.output.iterdir()):
                info = tf.gettarinfo(str(f), arcname=args.output.name+'/'+f.name)
                info.uid=info.gid=info.mtime=0
                info.uname=info.gname=''
                info.mode=0o644
                with f.open('rb') as stream:
                    tf.addfile(info,stream)
    parts=[]
    with archive.open('rb') as stream:
        while chunk := stream.read(8*1024*1024):
            name='kernel-upgrade.part'+format(len(parts)+1,'02d')
            (args.parts_dir/name).write_bytes(chunk)
            parts.append({'name':name,'size':len(chunk),'sha256':hashlib.sha256(chunk).hexdigest()})
    assert len(parts) <= 20
    for stale in args.parts_dir.glob('kernel-upgrade.part*'):
        if stale.name not in {part['name'] for part in parts}:
            stale.unlink()
    write_json(args.parts_dir/'bundle-parts.json',{'format':2,'release':u.RELEASE_PLAN,
        'payload_status':'ready','root':args.output.name,'archive_sha256':sha(archive),
        'installer_sha256':sha(dest_installer),'manifest_sha256':sha(manifest),'parts':parts})
    print(f'{archive.name}: {len(parts)} parts, SHA-256 {sha(archive)}')


if __name__ == '__main__':
    main()
