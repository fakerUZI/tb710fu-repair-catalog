#!/usr/bin/env python3
"""Exercise existing Wi-Fi profile over USB; record timings, no credentials.

Disconnects/reconnects Wi-Fi. Requires an active USB-NCM rescue connection.
No access point settings, security settings, or saved profiles are changed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time


def run(*args, timeout=15):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=timeout).stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=int, default=300)
    p.add_argument('--cycles', type=int, default=10)
    a = p.parse_args()
    assert Path('/sys/class/net/usb0/operstate').read_text().strip() == 'up'
    assert '172.16.42.1/' in run('ip', '-4', '-o', 'addr', 'show', 'dev', 'usb0')
    assert Path('/sys/module/ath12k/parameters/tb_vdev_count_fix').read_text().strip() == '1'
    devices = run('nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status').splitlines()
    devices = [x.split(':')[0] for x in devices if ':wifi:connected' in x]
    assert len(devices) == 1
    iface = devices[0]
    profile = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', iface).strip()
    assert re.fullmatch('[0-9a-fA-F-]{36}', profile)
    cursor = run('journalctl', '-k', '-b', '-n', '0', '--show-cursor', '--no-pager').strip()
    cursor = cursor.split('-- cursor: ')[1]
    started = time.monotonic()
    result = {'reconnects': 0, 'queries': 0, 'query_ms': [], 'https_status': []}

    def signal():
        before = time.monotonic()
        text = run('iw', 'dev', iface, 'station', 'dump', timeout=8)
        elapsed = round((time.monotonic()-before)*1000, 2)
        assert re.search(r'^\s*signal:\s*-?\d+', text, re.M), 'Signal field missing'
        assert elapsed < 1000, 'Station statistics request took >= 1 second'
        result['queries'] += 1
        result['query_ms'].append(elapsed)

    def internet():
        code = run('curl', '--noproxy', '*', '--interface', iface, '-sS', '-o', '/dev/null',
                   '-w', '%{http_code}', '--max-time', '15', 'https://www.kernel.org/', timeout=18)
        assert 200 <= int(code) < 400
        result['https_status'].append(int(code))

    try:
        signal()
        internet()
        for _ in range(a.cycles):
            run('nmcli', 'device', 'disconnect', iface)
            run('nmcli', 'connection', 'up', 'uuid', profile, 'ifname', iface, timeout=35)
            result['reconnects'] += 1
            for _ in range(3):
                signal()
                time.sleep(.5)
            print(json.dumps({'reconnects_completed': result['reconnects'],
                              'last_query_ms': result['query_ms'][-1]}), flush=True)
        while time.monotonic()-started < a.seconds:
            signal()
            time.sleep(2)
        internet()
    finally:
        state = run('nmcli', '-g', 'GENERAL.STATE', 'device', 'show', iface)
        if '100' not in state:
            run('nmcli', 'connection', 'up', 'uuid', profile, 'ifname', iface, timeout=35)
    logs = run('journalctl', '-k', '-b', '--after-cursor', cursor, '--no-pager', '-o', 'cat')
    result['stats_timeout_count'] = logs.count('time out while waiting for fw stats done')
    result['max_query_ms'] = max(result['query_ms'])
    result['elapsed_seconds'] = round(time.monotonic()-started, 2)
    del result['query_ms']
    print(json.dumps(result, sort_keys=True), flush=True)
    assert result['stats_timeout_count'] == 0, 'Statistics timeout recurred'


if __name__ == '__main__':
    main()
