"""Steam depot/DLC discovery inside the guarded, authenticated worker."""
import copy
from dataclasses import replace
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


def store_package_ids(network,app):
    """Public store packages are selection metadata, never an ownership gate."""
    from .providers import Transport
    try:
        response=json.loads(Transport(network).request(f'https://store.steampowered.com/api/appdetails?appids={app}&l=english'))
        entry=response.get(str(app),{})
        packages=entry.get('data',{}).get('packages',[]) if entry.get('success') else []
        if not isinstance(packages,list) or len(packages)>64:return []
        return list(dict.fromkeys(value for value in packages if type(value) is int and 0<value<2**32))
    except (ValueError,RuntimeError,TypeError,AttributeError):return []


def fetch_app_info(network, app, username):
    Request(app, 1).arguments()
    if not username.strip(): raise ValueError('Authenticate with Steam before loading DLC.')
    wallet = SteamSessionWallet(username)
    saved = wallet.read()
    if not saved: raise ValueError('Authenticate with Steam before loading DLC.')
    data = base64.b64decode(saved['data'], validate=True)
    if len(data) > 4 * 1024 * 1024: raise ValueError('Saved Steam session exceeds the size limit.')
    packages=store_package_ids(network,app)
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
                            '-loginid', str(secrets.randbelow(2**32-1)+1), '-remember-password',
                            '-package-ids', ','.join(map(str,packages))]
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
        allowed=entry.get('package_depots')
        if allowed is not None and (not isinstance(allowed,list) or len(allowed)>8192 or any(type(value) is not int or not 0<value<2**32 for value in allowed)):
            raise ValueError('Steam returned invalid package depot information.')
        if entry is not info['game']:
            if entry['id'] in seen: raise ValueError('Steam returned duplicate DLC entries.')
            seen.add(entry['id'])


def depot_entries(info):
    depots = info.get('depots')
    if not isinstance(depots, dict): return {}
    return {int(key):value for key,value in depots.items() if key.isdecimal() and 0 < int(key) < 2**32 and isinstance(value, dict) and (value.get('depotfromapp') or isinstance(value.get('manifests'),dict) or isinstance(value.get('encryptedmanifests'),dict))}


def matches_platform(depot, platform, language='english', architecture='64'):
    config = depot.get('config', {})
    if not isinstance(config, dict): return False
    oslist = [item.strip().lower() for item in str(config.get('oslist','')).split(',')]
    return (oslist == [''] or platform in oslist) and str(config.get('language','')).lower() in ('',language) and str(config.get('osarch','')) in ('',architecture) and str(config.get('lowviolence','0')) != '1'


def content_depots(info, dlc=None):
    if dlc is None:
        return {key:value for key,value in depot_entries(info['game']).items() if not str(value.get('dlcappid','')).strip('0')}
    result = {key:value for key,value in depot_entries(info['game']).items() if str(value.get('dlcappid','')) == str(dlc['id'])}
    # Parent placement and properties take precedence over duplicate child entries.
    for key,value in depot_entries(dlc).items():result.setdefault(key,value)
    return result


def resolve_shared(info, fetch, limit=20):
    """Inherit shared-depot properties from the source app, without changing order."""
    result=copy.deepcopy(info);cache={row['id']:row for row in [result['game'],*result['dlc']]}
    requests=0
    def load(source):
        nonlocal requests
        if source not in cache:
            if requests>=limit:raise ValueError('Shared-depot discovery exceeded its limit.')
            requests+=1;fetched=fetch(source);validate_info(fetched,source)
            for row in [fetched['game'],*fetched['dlc']]:cache.setdefault(row['id'],row)
        return cache[source]
    def resolve(app,key,node,path):
        nonlocal requests
        source=node.get('depotfromapp')
        if not source:return node
        try:source=int(source)
        except (TypeError,ValueError):raise ValueError('Invalid shared-depot source app.')
        if not 0<source<2**32 or (source,key) in path:raise ValueError('Cyclic or invalid shared-depot metadata.')
        origin=depot_entries(load(source)).get(key)
        if origin is None:raise ValueError(f'Steam did not return shared depot {key} from app {source}.')
        inherited=resolve(source,key,origin,path|{(source,key)})
        # Steam shared depots inherit OS, language, architecture and DLC requirements.
        merged=dict(node);merged.update(inherited);merged['depotfromapp']=str(source)
        merged['_source_app']=inherited.get('_source_app',source)
        return merged
    entries=[result['game'],*result['dlc']];known={row['id'] for row in entries};cursor=0
    while cursor<len(entries):
        entry=entries[cursor];cursor+=1
        for key,node in depot_entries(entry).items():
            resolved=resolve(entry['id'],key,node,{(entry['id'],key)})
            entry['depots'][str(key)]=resolved
            dlc=int(resolved.get('dlcappid','0') or 0)
            if dlc and dlc not in known:
                if len(result['dlc'])>=100:raise ValueError('Shared-depot DLC discovery exceeded its limit.')
                child=load(dlc);known.add(dlc);result['dlc'].append(child);entries.append(child)
    return result


def manifest_id(node,branch='public'):
    manifests=node.get('manifests',{})
    value=manifests.get(branch) if isinstance(manifests,dict) else None
    # Steam uses the public depot manifest when the chosen branch has no override.
    if value is None and branch!='public':value=manifests.get('public') if isinstance(manifests,dict) else None
    encrypted=node.get('encryptedmanifests',{})
    if isinstance(encrypted,dict) and branch in encrypted:
        raise ValueError(f'Branch {branch} requires a password; protected branches are not supported yet.')
    if isinstance(value,dict):value=value.get('gid')
    try:gid=int(value)
    except (ValueError,TypeError):return None
    return gid if 0<gid<2**64 else None


def ordered_content(info, selected):
    """Keep base-app Steam order, inserting selected parent-managed DLC in place."""
    known={row['id']:row for row in info['dlc']}
    if set(selected)-known.keys():raise ValueError('Unknown DLC selection.')
    seen=set();result=[]
    allowed=info['game'].get('package_depots')
    for key,node in depot_entries(info['game']).items():
        dlc=int(node.get('dlcappid','0') or 0)
        if dlc and dlc not in selected:continue
        if not dlc and isinstance(allowed,list) and key not in allowed:continue
        owner=known.get(dlc,info['game']);seen.add(key)
        result.append((owner,key,dict(node,_download_app=info['game']['id'])))
    for entry in info['dlc']:
        if entry['id'] not in selected:continue
        for key,node in depot_entries(entry).items():
            if key not in seen:seen.add(key);result.append((entry,key,node))
    return result


def build_plan(info, packs, platform, selected, language='english', architecture='64', branch='public'):
    """Steam's depot ordering/filtering, with explicit user DLC selection."""
    if platform not in ('linux','windows','macos'):raise ValueError('Select a supported platform.')
    if architecture not in ('32','64') or not language or not branch:raise ValueError('Select language, architecture and branch.')
    plan=[]
    for entry,key,node in ordered_content(info,selected):
        if not matches_platform(node,platform,language,architecture):continue
        expected=manifest_id(node,branch)
        if expected is None and not node.get('depotfromapp'):continue
        source=int(node.get('_source_app',node.get('_download_app',entry['id'])))
        rows={row.id:row for row in packs.get(info['game']['id'],[])}
        rows.update({row.id:row for row in packs.get(entry['id'],[])})
        rows.update({row.id:row for row in packs.get(source,[])})
        row=rows.get(key)
        if row is None:raise ValueError(f'Manifest provider is missing depot {key} for {entry["name"]}. No download has started.')
        if row.manifest is None and row.key and expected is not None:row=replace(row,manifest=expected)
        if expected is not None and row.manifest!=expected:
            raise ValueError(f'Provider manifest for depot {key} does not match Steam branch {branch}. Refresh or choose another provider; no download has started.')
        plan.append((source,row,entry['name']))
    if not plan:raise ValueError('No downloadable depots match these content settings.')
    return plan


def prepare_content(info, base_rows, platform, selected, source, fetch, language='english', architecture='64', branch='public'):
    """Resolve every required manifest before handing a game to the download queue."""
    app=info['game']['id'];packs={app:list(base_rows)};available={row.id for row in base_rows}
    for entry,key,node in ordered_content(info,selected):
        if not matches_platform(node,platform,language,architecture) or key in available:continue
        if manifest_id(node,branch) is None and not node.get('depotfromapp'):continue
        owner=int(node.get('_source_app',entry['id']))
        if owner not in packs:
            try:
                packs[owner]=fetch(owner);available.update(row.id for row in packs[owner])
                entry.pop('manifest_error',None);entry.pop('manifest_provider',None)
            except Exception as error:
                entry['manifest_error']=str(error);entry['manifest_provider']=source
                title=entry['name'] or f'DLC {entry["id"]}'
                raise RuntimeError(f'{source} could not fetch manifests for {title} (App ID {owner}). {error} Unselect this DLC, or choose another manifest provider under Advanced.') from error
    return build_plan(info,packs,platform,selected,language,architecture,branch)
