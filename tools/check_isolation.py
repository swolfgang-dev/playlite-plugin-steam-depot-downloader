"""Exercise tunnel loss, forced fallback, DNS and namespace replacement offline.

Uses two private Docker bridges as a simulated tunnel. No external requests,
credentials, host firewall changes or existing containers are involved.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('downloader', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules['downloader'] = module
spec.loader.exec_module(module)
from downloader.network import IMAGE, GUARD


def run(*args, timeout=15):
    return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=timeout)


def command(*args, timeout=15):
    result = run(*args, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Docker test operation failed: ' + args[0])
    return result.stdout.strip()


def main():
    name = 'playlite-isolation-test-' + uuid.uuid4().hex[:8]
    bridge = name + '-tunnel'
    gateway = canary = worker = None
    count = 0
    try:
        command('network', 'create', bridge)
        canary = command('run', '-d', '--name', name + '-canary', '--network', bridge,
                         '--entrypoint', '/bin/sh', IMAGE, '-c',
                         'while true; do printf "HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok" | nc -l -p 8080; done & '
                         'while true; do nc -u -l -p 5353 >> /tmp/dns-canary; done')
        peer = json.loads(command('inspect', canary))[0]['NetworkSettings']['Networks'][bridge]['IPAddress']
        gateway = command('run', '-d', '--name', name, '--network', 'bridge',
                          '--cap-add', 'NET_ADMIN', '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
                          '--entrypoint', '/bin/sh', IMAGE, '-c', 'sleep 600')
        command('network', 'connect', bridge, gateway)
        command('exec', gateway, 'ip', 'link', 'set', 'eth1', 'name', 'tun0')
        worker = command('run', '-d', '--name', name + '-worker', '--network', 'container:' + gateway,
                         '--user', '65534:65534', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                         '--read-only', '--entrypoint', '/bin/sh', IMAGE, '-c', 'sleep 600')
        def expect(script, allowed, label):
            nonlocal count
            result = run('exec', worker, '/bin/sh', '-c', script)
            if (result.returncode == 0) != allowed:
                raise RuntimeError(label + ': unexpected traffic result')
            count += 1
            print('PASS:', label, flush=True)
        tcp = f'wget -q -T 2 -t 1 -O /dev/null http://{peer}:8080'
        expect(tcp, True, 'TCP positive control before guard')
        command('exec', gateway, '/bin/sh', '-c', GUARD)
        expect(tcp, True, 'TCP allowed through tun0')
        # Send a DNS-like UDP packet; reception, not nc exit status, is evidence.
        command('exec', worker, '/bin/sh', '-c', f'printf DNS-CANARY | nc -u -w 1 {peer} 5353')
        time.sleep(.2)
        received = command('exec', canary, 'cat', '/tmp/dns-canary')
        if 'DNS-CANARY' not in received:
            raise RuntimeError('UDP positive control did not arrive.')
        print('PASS: DNS/UDP positive control through tun0', flush=True)
        count += 1
        command('exec', gateway, 'ip', 'link', 'set', 'tun0', 'down')
        expect(tcp, False, 'TCP blocked when tunnel interface goes down')
        info = json.loads(command('inspect', gateway))[0]['NetworkSettings']['Networks']['bridge']
        command('exec', gateway, 'ip', 'route', 'replace', peer + '/32', 'via', info['Gateway'], 'dev', 'eth0')
        expect(tcp, False, 'TCP blocked with explicit eth0 fallback route')
        # Prove the UID REJECT rule counted the UDP attempt, even though no DNS
        # reply can be returned. Counters distinguish the guard from routing errors.
        def rejected():
            rows = command('exec', gateway, 'iptables', '-L', 'PLAYLITE_WORKER', '-nvx').splitlines()
            return int(next(row.split()[0] for row in rows if 'REJECT' in row))
        before = rejected()
        run('exec', worker, '/bin/sh', '-c', f'printf BLOCKED-DNS | nc -u -w 1 {peer} 5353')
        if rejected() <= before:
            raise RuntimeError('DNS/UDP attempt was not rejected by the worker guard.')
        if 'BLOCKED-DNS' in command('exec', canary, 'cat', '/tmp/dns-canary'):
            raise RuntimeError('DNS/UDP escaped after tunnel loss.')
        print('PASS: DNS/UDP rejected by guard after tunnel loss', flush=True)
        count += 1
        command('exec', gateway, 'ip', 'route', 'del', peer + '/32')
        command('exec', gateway, 'ip', 'link', 'set', 'tun0', 'up')
        expect(tcp, True, 'TCP resumes after tunnel recovery')
        command('restart', gateway, timeout=35)
        expect(tcp, False, 'Old worker blocked after gateway namespace replacement')
        if command('exec', gateway, 'cat', '/proc/sys/net/ipv6/conf/all/disable_ipv6') != '1':
            raise RuntimeError('IPv6 is not disabled after restart.')
        print('PASS: IPv6 remains disabled after gateway restart', flush=True)
        count += 1
        print('Completed', count, 'isolation checks.', flush=True)
    finally:
        for cid in [worker, gateway, canary]:
            if cid: run('rm', '-f', cid)
        run('network', 'rm', bridge)


if __name__ == '__main__':
    main()
