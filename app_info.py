"""Steam depot/DLC discovery inside the guarded, authenticated worker."""
import base64
import json
from pathlib import Path
import secrets
import selectors
import os
import time
import subprocess
import tempfile
import uuid
from .credentials import SteamSessionWallet
from .worker import Request, worker_args


def fetch_app_info(network, app, username):
    Request(app, 1).arguments()
    if not username.strip(): raise ValueError('Authenticate with Steam before loading DLC.')
    wallet = SteamSessionWallet(username)
    saved = wallet.read()
    if not saved: raise ValueError('Authenticate with Steam before loading DLC.')
    data = base64.b64decode(saved['data'], validate=True)
    if len(data) > 4 * 1024 * 1024: raise ValueError('Saved Steam session exceeds the size limit.')
    name = 'playlite-appinfo-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='playlite-appinfo-') as directory:
        auth = Path(directory) / 'auth'; auth.mkdir(mode=0o777); auth.chmod(0o777)
        cache = auth / 'account.config'; cache.write_bytes(data); cache.chmod(0o666)
        output = Path(directory) / 'output'; output.mkdir()
        args = worker_args(network, Request(app, 1), output, username=username)
        args[args.index('--workdir') + 1] = '/auth'
        index = args.index('playlite-depot-worker:test')
        args[index:index] = ['--mount', f'type=bind,src={auth},dst=/auth']
        index = args.index('playlite-depot-worker:test')
        args[index + 1:] = ['-app-info', '-app', str(app), '-username', username.strip(),
                            '-loginid', str(secrets.randbelow(2**32-1)+1), '-remember-password']
        args[1:1] = ['--name', name]
        try:
            code, output = metadata_output(['docker', *args])
            lines = [line[len('PLAYLITE_APPINFO '):] for line in output.splitlines() if line.startswith('PLAYLITE_APPINFO ')]
            if code or len(lines) != 1:
                raise RuntimeError('Steam content information could not be loaded. Check Steam authentication and update the native worker.')
            if len(lines[0]) > 4 * 1024 * 1024: raise ValueError('Steam content information exceeds the size limit.')
            info = json.loads(lines[0]); validate_info(info, app)
            wallet.save({'data':base64.b64encode(cache.read_bytes()).decode()})
            return info
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)


def metadata_output(arguments, timeout=180, limit=4*1024*1024):
    """Bound output and abort interactive challenges instead of flooding on EOF."""
    process=subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    output=bytearray(); deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline: raise RuntimeError('Steam content information timed out.')
                for key,_ in selector.select(.2):
                    data=os.read(key.fileobj.fileno(),65536)
                    if not data:
                        selector.unregister(key.fileobj);continue
                    previous=bytes(output[-180:]);output.extend(data)
                    if len(output)>limit:raise ValueError('Steam content information exceeds the size limit.')
                    prompt=(previous+data).lower()
                    if b'enter account password' in prompt or b'steam guard!' in prompt:
                        raise ValueError('Steam requires authentication. Open Authenticate → Steam and sign in again.')
        return process.wait(timeout=max(.1,deadline-time.monotonic())),output.decode('utf-8',errors='replace')
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=5)
        process.stdout.close()


def validate_info(info, app):
    if not isinstance(info, dict) or not isinstance(info.get('game'), dict) or info['game'].get('id') != app:
        raise ValueError('Steam returned content information for a different game.')
    if not isinstance(info.get('dlc'), list) or len(info['dlc']) > 100:
        raise ValueError('Steam returned an invalid DLC list.')
    seen = {app}
    for entry in [info['game'], *info['dlc']]:
        if not isinstance(entry, dict) or type(entry.get('id')) is not int or not 0 < entry['id'] < 2**32 or not isinstance(entry.get('name'), str) or not isinstance(entry.get('depots'), (dict,str)):
            raise ValueError('Steam returned invalid content information.')
        if entry is not info['game']:
            if entry['id'] in seen: raise ValueError('Steam returned duplicate DLC entries.')
            seen.add(entry['id'])


def depot_entries(info):
    depots = info.get('depots')
    if not isinstance(depots, dict): return {}
    return {int(key):value for key,value in depots.items() if key.isdecimal() and 0 < int(key) < 2**32 and isinstance(value, dict) and (value.get('depotfromapp') or (isinstance(value.get('manifests'),dict) and value['manifests'].get('public')))}


def matches_platform(depot, platform):
    config = depot.get('config', {})
    if not isinstance(config, dict): return False
    oslist = str(config.get('oslist','')).split(',')
    return (oslist == [''] or platform in oslist) and config.get('language','english') in ('','english') and str(config.get('osarch','64')) in ('','64') and str(config.get('lowviolence','0')) != '1'


def content_depots(info, dlc=None):
    if dlc is None:
        return {key:value for key,value in depot_entries(info['game']).items() if not str(value.get('dlcappid','')).strip('0')}
    result = {key:value for key,value in depot_entries(info['game']).items() if str(value.get('dlcappid','')) == str(dlc['id'])}
    result.update(depot_entries(dlc))
    return result


def build_plan(info, packs, platform, selected):
    """Include common/platform depots and selected DLC; never guess associations."""
    entries = [info['game'], *[row for row in info['dlc'] if row['id'] in selected]]
    if platform not in ('linux','windows','macos'): raise ValueError('Select a supported platform.')
    if set(selected) - {row['id'] for row in info['dlc']}: raise ValueError('Unknown DLC selection.')
    plan = []; seen = set()
    for entry in entries:
        depots = content_depots(info, None if entry is info['game'] else entry)
        applicable = {key:row for key,row in depots.items() if matches_platform(row, platform)}
        rows = {row.id:row for row in packs.get(info['game']['id'], [])}
        rows.update({row.id:row for row in packs.get(entry['id'], [])})
        for key in applicable:
            if key in seen: continue
            if key not in rows: raise ValueError(f'Manifest provider is missing depot {key} for {entry["name"]}. No download has started.')
            seen.add(key); plan.append((entry['id'], rows[key], entry['name']))
    if not plan: raise ValueError('No downloadable depots match this platform.')
    return plan
