#!/usr/bin/env python3
"""Test IPv4/IPv6 legacy+nft rules and real UDP NAT in disposable netns.

No rules or interfaces are changed in the host network namespace. Run as root.
Kernel module autoloading still occurs and is intentionally tested.
"""
import argparse
import json
import socket
import subprocess
import sys


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=8)


def exercise(backend):
    ipv6 = backend.startswith('ip6')
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    local, virtual, rewritten = ('::1', '::2', '::3') if ipv6 else (
        '127.0.0.1', '127.0.0.2', '127.0.0.3')
    run('ip', 'link', 'set', 'lo', 'up')
    if ipv6:
        for addr in [virtual, rewritten]:
            run('ip', '-6', 'addr', 'add', addr+'/128', 'dev', 'lo', 'nodad')
    run(backend, '-w', '3', '-t', 'nat', '-L')

    def rule(table, chain, port, target, *options):
        run(backend, '-w', '3', '-t', table, '-A', chain, '-p', 'udp',
            '--dport', str(port), '-j', target, *options)

    def deliver(server_port, destination_port, source=None, expected_source=None):
        with socket.socket(family, socket.SOCK_DGRAM) as server, \
             socket.socket(family, socket.SOCK_DGRAM) as client:
            server.bind((local, server_port))
            server.settimeout(2)
            if source:
                client.bind((source, 0))
            client.sendto(b'tb710fu-netfilter-proof',
                          (local if source else virtual, destination_port))
            data, peer = server.recvfrom(256)
            assert data == b'tb710fu-netfilter-proof'
            if expected_source:
                assert peer[0] == expected_source, (peer, expected_source)

    destination = f'[{local}]:31501' if ipv6 else f'{local}:31501'
    rule('nat', 'OUTPUT', 31502, 'DNAT', '--to-destination', destination)
    deliver(31501, 31502)
    rule('nat', 'OUTPUT', 31504, 'REDIRECT', '--to-ports', '31503')
    deliver(31503, 31504)
    rule('nat', 'POSTROUTING', 31505, 'SNAT', '--to-source', rewritten)
    deliver(31505, 31505, source=virtual, expected_source=rewritten)
    # Keep a listening destination so an ICMP rejection cannot be attributed
    # to a naturally closed UDP port.
    with socket.socket(family, socket.SOCK_DGRAM) as server, \
         socket.socket(family, socket.SOCK_DGRAM) as client:
        server.bind((local, 31507))
        server.settimeout(.2)
        client.connect((local, 31507))
        client.settimeout(2)
        rule('filter', 'OUTPUT', 31507, 'REJECT')
        try:
            client.send(b'blocked')
            client.recv(16)
        except (ConnectionRefusedError, PermissionError):
            # LOCAL_OUT rejection may fail send() synchronously with EPERM,
            # rather than delivering ECONNREFUSED to the later recv().
            pass
        else:
            raise AssertionError('REJECT did not return ICMP port unreachable')
        try:
            server.recvfrom(16)
        except TimeoutError:
            pass
        else:
            raise AssertionError('REJECT allowed delivery')
    # Additional targets/tables requested by the report: insertion coverage.
    rule('nat', 'OUTPUT', 31509, 'NETMAP', '--to', '::/120' if ipv6 else '127.0.0.0/24')
    for table in ['filter', 'mangle', 'raw', 'nat']:
        run(backend, '-w', '3', '-t', table, '-S')
    return {'backend': backend, 'DNAT': 'UDP delivered', 'REDIRECT': 'UDP delivered',
            'SNAT': 'source rewritten', 'REJECT': 'UDP delivery rejected',
            'NETMAP': 'rule inserted', 'tables': ['filter', 'mangle', 'raw', 'nat']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['iptables-legacy', 'ip6tables-legacy',
                                             'iptables-nft', 'ip6tables-nft'])
    args = parser.parse_args()
    if args.backend:
        print(json.dumps(exercise(args.backend)))
        return
    failures = []
    for backend in ['iptables-legacy', 'ip6tables-legacy', 'iptables-nft', 'ip6tables-nft']:
        result = subprocess.run(['unshare', '--net', sys.executable, __file__,
                                 '--backend', backend], text=True, capture_output=True, timeout=45)
        print(result.stdout, end='')
        if result.returncode:
            failures.append(backend)
            print(backend+': '+result.stderr, file=sys.stderr)
    if failures:
        raise SystemExit('Failed: '+', '.join(failures))


if __name__ == '__main__':
    main()
