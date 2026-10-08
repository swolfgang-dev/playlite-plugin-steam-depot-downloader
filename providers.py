"""Moon-compatible HTTP providers and strictly data-only manifest pack parsing."""
import base64
from dataclasses import dataclass, field
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import uuid
import zipfile
from .constants import IMAGE

MAX_PACK = 64 * 1024 * 1024
SOURCES = {
    'Luie': 'https://lua.tools/api/manifest/download?appid={app}&source=Luie',
    'Hubcap': 'https://hubcapmanifest.com/api/v1/manifest/{app}?api_key={key}',
    'Sushi': 'https://raw.githubusercontent.com/sushi-dev55-alt/sushitools-games-repo-alt/refs/heads/main/{app}.zip',
    'Ryuu': 'http://167.235.229.108/{app}',
}
# Executes inside the guarded container. Secrets arrive on stdin, never argv.
HTTP_SCRIPT = r'''
import base64,json,sys,urllib.request,urllib.error
request=json.load(sys.stdin)
try:
    req=urllib.request.Request(request['url'], data=base64.b64decode(request['body']) if request.get('body') else None, headers={'User-Agent':'Playlite-Steam-Depot-Downloader/0.1 (Moon-compatible)', 'Accept-Encoding':'identity', **request.get('headers', {})})
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if request.get('headers') or request.get('body'):
                raise ValueError('Authenticated redirects are not accepted')
            if not newurl.startswith('https://'):
                raise ValueError('Insecure redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    with urllib.request.build_opener(Redirect()).open(req, timeout=30) as response:
        body=response.read(67108865)
        if len(body)>67108864: raise ValueError('Response exceeds limit')
        print(json.dumps({'status':response.status,'body':base64.b64encode(body).decode()}))
except urllib.error.HTTPError as error:
    body=error.read(4096).decode('utf-8',errors='replace').lower()
    reason='invalid_api_key' if 'invalid api key' in body else 'expired_code' if 'expired' in body else 'invalid_code' if 'invalid' in body and 'code' in body else 'browser_challenge' if 'cloudflare' in body or 'just a moment' in body or 'error code: 1010' in body else ''
    print(json.dumps({'status':error.code,'body':'','reason':reason}))
except Exception:
    print(json.dumps({'status':0,'body':''}))
'''

class Transport:
    def __init__(self, network, image='playlite-depot-worker:test'):
        self.network, self.image = network, image

    def request(self, url, headers=None, data=None):
        self.network.check()
        if getattr(self.network, 'is_vm', False) is True:
            response = self.network.http(url, headers, data)
        else:
            response = self._docker_request(url, headers, data)
        status = response['status']
        if status != 200:
            reason = response.get('reason', '')
            if reason == 'invalid_api_key':
                raise RuntimeError('LuaTools rejected its public API client key; the provider login configuration needs updating.')
            if reason == 'browser_challenge':
                raise RuntimeError('The provider blocked the isolated HTTP worker with a browser challenge.')
            if reason == 'expired_code':
                raise RuntimeError('The login code expired or was already used. Generate a fresh Discord /login code.')
            if reason == 'invalid_code':
                raise RuntimeError('The provider rejected the login code.')
            if status in (401, 403):
                raise RuntimeError('Provider authentication was rejected. Sign in again or check the provider key.')
            raise RuntimeError(f'Provider request failed (HTTP {status or "connection error"}).')
        return base64.b64decode(response['body'], validate=True)

    def _docker_request(self, url, headers, data):
        args = self.network.probe_args('')
        name = 'playlite-manifest-' + uuid.uuid4().hex
        args[1:1] = ['--name', name, '-i']
        args[args.index('--entrypoint') + 1] = 'python3'
        args[args.index('64m')] = '384m'
        index = args.index(IMAGE)
        args[index:] = [self.image, '-c', HTTP_SCRIPT]
        payload = json.dumps({'url':url, 'headers':headers or {}, 'body':base64.b64encode(json.dumps(data).encode()).decode() if data is not None else None})
        try:
            result = subprocess.run(['docker', *args], input=payload, capture_output=True, text=True, timeout=45)
            if result.returncode:
                raise RuntimeError('The isolated HTTP worker failed. Run isolated Steam setup in downloader settings.')
            return json.loads(result.stdout)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)

    def fetch(self, source, app, credential=''):
        if type(app) is not int or not 0 < app < 2**32:
            raise ValueError('Enter a valid Steam App ID.')
        if source not in SOURCES:
            raise ValueError('Select a manifest provider.')
        if source in ('Luie', 'Hubcap') and not credential:
            raise ValueError('Sign into Moon or enter a Hubcap API key first.')
        from urllib.parse import quote
        url = SOURCES[source].format(app=app, key=quote(credential, safe=''))
        headers = {'Authorization':'Bearer ' + credential} if source == 'Luie' else None
        data = self.request(url, headers)
        return parse_pack(data, app)

@dataclass(frozen=True)
class Depot:
    id: int
    manifest: int | None
    data: bytes = field(repr=False)
    key: str | None = field(default=None, repr=False)
    name: str = ''


def parse_pack(data, app):
    """Never extract or execute Lua; reject traversal, duplicate names and bombs."""
    if len(data) > MAX_PACK:
        raise ValueError('Manifest pack exceeds the size limit.')
    if data.startswith(b'\x1f\x8b'):
        import gzip
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                data = compressed.read(MAX_PACK + 1)
            if len(data) > MAX_PACK: raise ValueError('Expanded response exceeds the size limit.')
        except OSError as error:
            raise ValueError('Provider returned a corrupt compressed response.') from error
    keys, pins, manifests, names = {}, {}, {}, set()
    app_ids=set();depot_names={}
    def read_lua(lua):
        if len(lua)>2*1024*1024:raise ValueError('Lua metadata exceeds the size limit.')
        encoding='utf-16' if lua.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        try:text=lua.decode(encoding)
        except UnicodeError as error:raise ValueError('Unsupported Lua metadata encoding.') from error
        # Data extraction only: never import, evaluate or execute provider scripts.
        for line in text.splitlines():
            label=re.search(r'addappid\s*\(\s*(\d+).*?\)\s*--\s*(.+)$',line)
            if label:depot_names[int(label[1])]=label[2].strip()[:200]
        app_ids.update(map(int,re.findall(r'addappid\s*\(\s*(\d+)\s*(?:\)|,)',text)))
        for depot,key in re.findall(r"addappid\s*\(\s*(\d+)\s*,\s*\d+\s*,\s*['\"]([0-9a-fA-F]{64})['\"]\s*\)",text):
            depot=int(depot)
            if not 0<depot<2**32:raise ValueError('Invalid depot ID.')
            if depot in keys and keys[depot]!=key.lower():raise ValueError('Conflicting depot keys.')
            keys[depot]=key.lower()
        for match in re.finditer(r"setManifestid\s*\(\s*(\d+)\s*,\s*(?:['\"](\d+)['\"]|(\d+))",text):
            depot=int(match[1]);manifest=int(match[2] or match[3])
            if not 0<depot<2**32 or not 0<manifest<2**64:raise ValueError('Invalid manifest pin.')
            if depot in pins and pins[depot]!=manifest:raise ValueError('Conflicting manifest pins.')
            pins[depot]=manifest
    if not zipfile.is_zipfile(io.BytesIO(data)):
        if data.lstrip().lower().startswith((b'<!doctype',b'<html',b'{',b'[')) or data.startswith((b'Rar!',b'7z\xbc\xaf\x27\x1c')):
            kind='HTML page' if data.lstrip().lower().startswith((b'<!doctype',b'<html')) else 'JSON response' if data.lstrip().startswith((b'{',b'[')) else 'unsupported archive'
            raise ValueError(f'Provider returned a {kind} instead of Lua metadata or a ZIP manifest pack.')
        read_lua(data)
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist())>4096 or sum(item.file_size for item in archive.infolist())>MAX_PACK:
                    raise ValueError('Manifest pack exceeds the expanded size limit.')
                for item in archive.infolist():
                    normalized=item.filename.replace('\\','/')
                    name=PurePosixPath(normalized)
                    if name.is_absolute() or '..' in name.parts or ':' in normalized or normalized in names or (item.external_attr>>16)&0o170000==0o120000:
                        raise ValueError('Unsafe manifest pack entry.')
                    names.add(normalized)
                    if item.is_dir():continue
                    if name.suffix.lower()=='.lua':read_lua(archive.read(item))
                    elif name.suffix.lower()=='.manifest':
                        match=re.fullmatch(r'(\d+)_(\d+)\.manifest',name.name)
                        if match:
                            pair=tuple(map(int,match.groups()));blob=archive.read(item)
                            if pair in manifests and manifests[pair]!=blob:raise ValueError('Conflicting manifest files.')
                            manifests[pair]=blob
        except (zipfile.BadZipFile,UnicodeError) as error:raise ValueError('Provider returned an invalid manifest pack.') from error
    if app not in app_ids:
        raise ValueError("Manifest pack does not declare the requested Steam App ID.")
    rows = []
    for (depot, manifest), blob in sorted(manifests.items()):
        if not 0 < depot < 2**32 or not 0 < manifest < 2**64 or not blob:
            raise ValueError('Invalid depot or manifest in pack.')
        if depot in pins and pins[depot] != manifest: continue
        rows.append(Depot(depot, manifest, blob, keys.get(depot), depot_names.get(depot, '')))
    binary_ids={row.id for row in rows}
    for depot in sorted((keys.keys()|pins.keys())-binary_ids):
        rows.append(Depot(depot,pins.get(depot),b'',keys.get(depot),depot_names.get(depot,'')))
    if not rows:raise ValueError('No usable depot keys or manifests were found in the provider response.')
    return rows


def prepare_depot(row, directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    if row.data:
        (directory/'manifest.bin').write_bytes(row.data)
        (directory/'manifest.bin').chmod(0o644)
    if row.key:
        (directory / 'depot.keys').write_text(f'{row.id};{row.key}\n')
        (directory / 'depot.keys').chmod(0o644)
    # Parent directory remains private; the bind mount is readable by worker UID.
    directory.chmod(0o755)
