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
    req=urllib.request.Request(request['url'], data=base64.b64decode(request['body']) if request.get('body') else None, headers=request.get('headers', {}))
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
    print(json.dumps({'status':error.code,'body':''}))
except Exception:
    print(json.dumps({'status':0,'body':''}))
'''

class Transport:
    def __init__(self, network, image='playlite-depot-worker:test'):
        self.network, self.image = network, image

    def request(self, url, headers=None, data=None):
        self.network.check()
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
                raise RuntimeError('The isolated HTTP worker failed. Build the native worker image first.')
            response = json.loads(result.stdout)
            status = response['status']
            if status != 200:
                if status in (401, 403):
                    raise RuntimeError('Provider authentication was rejected. Sign in again or check the provider key.')
                raise RuntimeError(f'Provider request failed (HTTP {status or "connection error"}).')
            return base64.b64decode(response['body'], validate=True)
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
        return parse_pack(self.request(url, headers), app)

@dataclass(frozen=True)
class Depot:
    id: int
    manifest: int
    data: bytes = field(repr=False)
    key: str | None = field(default=None, repr=False)


def parse_pack(data, app):
    """Never extract or execute Lua; reject traversal, duplicate names and bombs."""
    if len(data) > MAX_PACK:
        raise ValueError('Manifest pack exceeds the size limit.')
    keys, pins, manifests, names = {}, {}, {}, set()
    app_ids = set()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 4096 or sum(info.file_size for info in archive.infolist()) > MAX_PACK:
                raise ValueError('Manifest pack exceeds the expanded size limit.')
            for info in archive.infolist():
                name = PurePosixPath(info.filename)
                if name.is_absolute() or '..' in name.parts or '\\' in info.filename or info.filename in names or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Unsafe manifest pack entry.')
                names.add(info.filename)
                if info.is_dir(): continue
                if name.suffix.lower() == '.lua':
                    text = archive.read(info).decode('utf-8-sig')
                    app_ids.update(map(int, re.findall(r'addappid\s*\(\s*(\d+)\s*\)', text)))
                    for depot, key in re.findall(r'addappid\s*\(\s*(\d+)\s*,\s*\d+\s*,\s*[\'"]([0-9a-fA-F]{64})[\'"]\s*\)', text):
                        depot = int(depot)
                        if depot in keys and keys[depot] != key.lower(): raise ValueError('Conflicting depot keys.')
                        keys[depot] = key.lower()
                    for depot, manifest in re.findall(r'setManifestid\s*\(\s*(\d+)\s*,\s*[\'"](\d+)[\'"]', text):
                        depot, manifest = int(depot), int(manifest)
                        if depot in pins and pins[depot] != manifest: raise ValueError('Conflicting manifest pins.')
                        pins[depot] = manifest
                elif name.suffix.lower() == '.manifest':
                    match = re.fullmatch(r'(\d+)_(\d+)\.manifest', name.name)
                    if match:
                        pair = tuple(map(int, match.groups()))
                        blob = archive.read(info)
                        if pair in manifests and manifests[pair] != blob: raise ValueError('Conflicting manifest files.')
                        manifests[pair] = blob
    except (zipfile.BadZipFile, UnicodeError) as error:
        raise ValueError('Provider returned an invalid manifest pack.') from error
    if app not in app_ids:
        raise ValueError("Manifest pack does not declare the requested Steam App ID.")
    rows = []
    for (depot, manifest), blob in sorted(manifests.items()):
        if not 0 < depot < 2**32 or not 0 < manifest < 2**64 or not blob:
            raise ValueError('Invalid depot or manifest in pack.')
        if depot in pins and pins[depot] != manifest: continue
        rows.append(Depot(depot, manifest, blob, keys.get(depot)))
    if not rows: raise ValueError('No usable binary depot manifests were found in this pack.')
    return rows


def prepare_depot(row, directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    (directory / 'manifest.bin').write_bytes(row.data)
    if row.key:
        (directory / 'depot.keys').write_text(f'{row.id};{row.key}\n')
    # Parent directory remains private; the bind mount is readable by worker UID.
    directory.chmod(0o755)
