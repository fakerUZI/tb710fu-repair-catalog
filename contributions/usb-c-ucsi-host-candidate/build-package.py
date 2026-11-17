#!/usr/bin/env python3
"""Build a reproducible review-only .tbfix; never upload or execute its contents."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
IDENT = 'usb-c-ucsi-host-candidate'
TITLE = 'USB-C UCSI 协商 host 候选与测试（issue #19，待实测）'
ATTACHMENTS = ('usb-c-test.py', 'test-usb-c.py', 'README.md', 'VALIDATION.md', 'source-evidence.json')


def build():
    metadata = {'id': IDENT, 'title': TITLE,
                'description': (ROOT / 'description.txt').read_text().strip(),
                'risk': (ROOT / 'risk.txt').read_text().strip(),
                'requires_admin': True, 'attachments': list(ATTACHMENTS)}
    files = {'script.sh': (ROOT / 'script.sh').read_bytes()}
    for name in ATTACHMENTS:
        files['attachments/' + name] = (ROOT / 'attachments' / name).read_bytes()
    assert len(files) <= 31 and all(len(data) <= 10 * 1024 * 1024 for data in files.values())
    assert sum(map(len, files.values())) <= 30 * 1024 * 1024
    dest = ROOT / (IDENT + '-20261117.tbfix')
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, data in {'submission.json': (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode(), **files}.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 17, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, data)
    prefix = 'contributions/' + IDENT + '/'
    record = {k: v for k, v in metadata.items() if k not in ('requires_admin', 'attachments')}
    record.update(device='TB710FU', status='community-review')
    def entry(name, data):
        return {'path': prefix + name, 'sha256': hashlib.sha256(data).hexdigest()}
    record['script'] = dict(entry('script.sh', files['script.sh']), requires_admin=True)
    record['attachments'] = [entry(name, data) for name, data in files.items() if name.startswith('attachments/')]
    (ROOT / 'catalog-entry.candidate.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    (ROOT / (dest.name + '.sha256')).write_text(hashlib.sha256(dest.read_bytes()).hexdigest() + '  ' + dest.name + '\n')
    print(dest)


if __name__ == '__main__':
    build()
