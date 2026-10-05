"""Opt-in live VPN failure tests using a clone; never change the connected gateway.

Reuses its read-only secret mount without reading/printing credential contents.
Only diagnostic HTTPS/DNS traffic is sent. No game downloads.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('downloader', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules['downloader'] = module
spec.loader.exec_module(module)
from downloader.network import Network, IMAGE, LABEL, GUARD


def run(*args, timeout=25):
    return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=timeout)


def command(*args, timeout=25):
    result = run(*args, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Docker operation failed: ' + args[0])
    return result.stdout.strip()


def wait_ready(network, timeout=90):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        data = network.inspect()
        logs = run('logs', '--tail', '80', network.container)
        if data['State'].get('Health', {}).get('Status') == 'healthy':
            try:
                result = network.check()
            except RuntimeError:
                time.sleep(1)
                continue
            print(result, flush=True)
            return
        time.sleep(1)
    raise RuntimeError('Test VPN did not become healthy within 90 seconds. Authentication retries may still be in progress; the original gateway was not changed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize connecting a separate test VPN using the current gateway secret mount')
    parser.add_argument('--crash', action='store_true', help='Also force an OpenVPN crash and verify automatic recovery')
    parser.add_argument('--country', default='', help='Optional single country for the test gateway')
    options = parser.parse_args()
    if not options.live:
        parser.error('Pass --live to run the isolated live VPN failure tests.')
    source = Network().inspect()
    if source['State'].get('Health', {}).get('Status') != 'healthy':
        raise RuntimeError('Connect the plugin VPN before running the live tests.')
    secret = next(m for m in source['Mounts'] if m['Destination'] == '/run/secrets')
    if secret['RW']:
        raise RuntimeError('The source credential mount must be read-only.')
    suffix = uuid.uuid4().hex[:10]
    name = 'playlite-vpn-failure-test-' + suffix
    worker_name = name + '-worker'
    cid = None
    worker = None
    report = []
    with tempfile.TemporaryDirectory(prefix='playlite-vpn-probe-') as directory:
        network = Network()
        network.directory = directory
        try:
            args = ['run', '-d', '--name', name, '--label', LABEL + '=' + str(os.getuid()),
                    '--network', 'bridge', '--cap-add', 'NET_ADMIN', '--device', '/dev/net/tun',
                    '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1', '--mount',
                    f'type=bind,src={secret["Source"]},dst=/run/secrets,readonly']
            allowed = {'VPN_SERVICE_PROVIDER', 'VPN_TYPE', 'OPENVPN_PROTOCOL', 'SERVER_COUNTRIES',
                       'DNS_ADDRESS', 'FIREWALL', 'FIREWALL_OUTBOUND_SUBNETS'}
            for item in source['Config']['Env']:
                if item.split('=', 1)[0] in allowed and not (options.country and item.startswith('SERVER_COUNTRIES=')):
                    args += ['-e', item]
            if options.country:
                from downloader.settings import Preferences
                Preferences(options.country).validate()
                args += ['-e', 'SERVER_COUNTRIES=' + options.country]
            # Test-only control API bound to loopback; worker cannot access loopback.
            args += ['-e', 'HTTP_CONTROL_SERVER_ADDRESS=127.0.0.1:8000', '-e',
                     'HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE={"auth":"none"}']
            cid = command(*args, IMAGE)
            network.container = cid
            command('exec', cid, '/bin/sh', '-c', GUARD)
            wait_ready(network)
            worker_args = network.probe_args('sleep 600')
            worker_args[1:2] = ['-d', '--name', worker_name]
            worker = command(*worker_args)
            def probe(script, expected, label):
                result = run('exec', worker, '/bin/sh', '-c', script, timeout=20)
                if (result.returncode == 0) != expected:
                    raise RuntimeError(label + ': unexpected traffic result')
                report.append(label)
                print('PASS:', label, flush=True)
            tcp = 'wget -q -T 4 -t 1 -O /dev/null https://1.1.1.1'
            dns = 'timeout 8 nslookup example.com 103.86.96.100 >/dev/null 2>&1'
            probe(tcp, True, 'HTTPS works through connected tunnel')
            probe(dns, True, 'DNS works through connected tunnel')
            info = json.loads(command('inspect', cid))[0]['NetworkSettings']['Networks']['bridge']
            address, gateway = info['IPAddress'], info['Gateway']
            command('exec', '-d', cid, '/bin/sh', '-c', 'while true; do printf "HTTP/1.0 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nok" | nc -l -p 8080; done')
            probe(f'wget -q -T 2 -t 1 -O /dev/null http://{address}:8080', False, 'Local network access blocked while VPN connected')
            def vpn_status(status):
                command('exec', cid, 'wget', '-q', '-O', '/dev/null', '--method=PUT',
                        '--body-data=' + json.dumps({'status': status}), 'http://127.0.0.1:8000/v1/vpn/status')
            vpn_status('stopped')
            for _ in range(10):
                link = run('exec', cid, 'ip', 'link', 'show', 'tun0')
                if link.returncode or 'state DOWN' in link.stdout:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Test VPN tunnel did not stop.')
            probe(tcp, False, 'HTTPS blocked after OpenVPN stops')
            probe(dns, False, 'DNS blocked after OpenVPN stops')
            # Explicit fallback routes must still be rejected by the UID firewall.
            for target in ['1.1.1.1', '103.86.96.100']:
                command('exec', cid, 'ip', 'route', 'replace', target + '/32', 'via', gateway, 'dev', 'eth0')
            probe(tcp, False, 'HTTPS blocked with forced ordinary-network route')
            probe(dns, False, 'DNS blocked with forced ordinary-network route')
            probe(f'wget -q -T 2 -t 1 -O /dev/null http://{address}:8080', False, 'Local network blocked after tunnel loss')
            for target in ['1.1.1.1', '103.86.96.100']:
                command('exec', cid, 'ip', 'route', 'del', target + '/32')
            vpn_status('running')
            wait_ready(network)
            probe(tcp, True, 'HTTPS resumes after OpenVPN reconnects')
            probe(dns, True, 'DNS resumes after OpenVPN reconnects')
            if options.crash:
                def vpn_pid():
                    processes = command('exec', cid, 'ps', '-o', 'pid,comm').splitlines()
                    return next(row.split()[0] for row in processes if len(row.split()) == 2 and row.split()[1].startswith('openvpn'))
                old_pid = vpn_pid()
                command('exec', cid, 'kill', '-KILL', old_pid)
                wait_ready(network)
                if vpn_pid() == old_pid:
                    raise RuntimeError('OpenVPN process was not replaced after its crash.')
                print('PASS: Gluetun automatically replaces crashed OpenVPN process', flush=True)
                report.append('Automatic OpenVPN crash recovery')
                probe(tcp, True, 'HTTPS resumes after automatic crash recovery')
                probe(dns, True, 'DNS resumes after automatic crash recovery')
            # Docker recreates the gateway network namespace; an old worker must
            # not silently attach to the new one or escape through the old one.
            command('restart', cid, timeout=35)
            probe(tcp, False, 'Old worker stays blocked after gateway restart')
            probe(dns, False, 'Old worker DNS stays blocked after gateway restart')
            try:
                network.check()
            except RuntimeError:
                print('PASS: readiness rejects gateway before guard reinstallation', flush=True)
                report.append('Readiness rejects restarted gateway')
            else:
                raise RuntimeError('Restarted gateway passed readiness without reinstalled guard.')
            command('exec', cid, '/bin/sh', '-c', GUARD)
            wait_ready(network)
            probe(tcp, False, 'Old worker remains isolated after new gateway becomes ready')
            probe(dns, False, 'Old worker DNS remains isolated after recovery')
            result = run(*network.probe_args(tcp), timeout=20)
            if result.returncode:
                raise RuntimeError('Fresh worker cannot use recovered VPN.')
            print('PASS: fresh worker uses recovered gateway namespace', flush=True)
            report.append('Fresh worker uses recovered namespace')
        except subprocess.TimeoutExpired:
            # The canary server is detached below instead of waiting for its lifetime.
            raise RuntimeError('Test setup exceeded its deadline.')
        finally:
            if worker: run('rm', '-f', worker)
            if cid: run('rm', '-f', cid)
    print('Completed', len(report), 'live checks.', flush=True)


if __name__ == '__main__':
    main()
