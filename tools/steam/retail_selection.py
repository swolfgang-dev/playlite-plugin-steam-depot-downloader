"""Restrict integration-provided depots to public retail package membership."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def retail_packages(app):
    with urllib.request.urlopen(f'https://store.steampowered.com/api/appdetails?appids={app}&l=english',timeout=20) as response:
        raw=response.read(1048577)
    if len(raw)>1048576:raise RuntimeError('Steam store package information exceeds the limit.')
    entry=json.loads(raw).get(str(app),{})
    packages=entry.get('data',{}).get('packages') if entry.get('success') else None
    if not isinstance(packages,list) or not packages or len(packages)>64:
        raise RuntimeError('Steam retail package membership is unavailable. No depot selection was changed.')
    if any(type(value) is not int or not 0<value<2**32 for value in packages):
        raise RuntimeError('Steam returned invalid retail package IDs.')
    return list(dict.fromkeys(packages))


def package_depots(app,packages,records):
    allowed=set()
    for identifier in packages:
        entry=records.get(str(identifier))
        if not isinstance(entry,dict):raise RuntimeError('Steam has not returned all retail packages. Retry when the client is online.')
        apps=entry.get('appids',{})
        depots=entry.get('depotids',{})
        if not isinstance(apps,dict) or not isinstance(depots,dict):raise RuntimeError('Steam returned incomplete retail package metadata.')
        try:
            appids={int(value) for value in apps.values()}
            depotids={int(value) for value in depots.values()}
        except (ValueError,TypeError):raise RuntimeError('Steam returned invalid package membership.')
        if app not in appids:raise RuntimeError('Steam retail package does not contain this game.')
        if any(not 0<value<2**32 for value in depotids):raise RuntimeError('Steam returned invalid depot membership.')
        allowed.update(depotids)
    if not allowed:raise RuntimeError('Steam retail packages contain no depots. No depot selection was changed.')
    return allowed


def restrict_depots(home,app,allowed,depots):
    """Keep other games untouched and preserve originals outside watched locations."""
    home=Path(home)
    script=home/'.steam/steam/config/stplug-in'/f'{app}.lua'
    cache=home/'.config/SLSsteam/cache'
    source=script.read_text()
    backup=home/'.config/playlite/retail-originals'/str(app)
    ledger=backup/'selection.json'
    previous=json.loads(ledger.read_text()) if ledger.exists() else {}
    restoring=previous.get('filtered_sha256')==hashlib.sha256(source.encode()).hexdigest()
    if restoring:
        digest=previous.get('original_sha256','')
        if not re.fullmatch('[0-9a-f]{64}',digest):raise RuntimeError('Invalid original depot selection record.')
        source=(backup/(digest+'.lua')).read_text()
    if len(source)>1048576:raise RuntimeError('LuaMoon game script exceeds the selection limit.')
    # Shared dependencies and DLC retain their separate Steam selection rules.
    # App IDs appearing in a provider script must never be treated as depots.
    eligible={int(key) for key,value in depots.items() if str(key).isdecimal() and isinstance(value,dict)
              and not value.get('dlcappid') and not value.get('depotfromapp') and str(value.get('sharedinstall','0'))!='1'}
    if not eligible:raise RuntimeError('Steam game depot metadata is unavailable. No depot selection was changed.')
    candidates={}
    for path in cache.glob('depotkey_*.yaml'):
        text=path.read_text()
        owner=re.search(r'^appId:\s*(\d+)\s*$',text,re.M)
        depot=re.search(r'^depotId:\s*(\d+)\s*$',text,re.M)
        if owner and depot and int(owner[1])==app and int(depot[1]) in eligible and int(depot[1]) not in allowed:
            candidates[int(depot[1])]=path
    for match in re.finditer(r'\baddappid\s*\(\s*(\d+)\s*,\s*1\s*,\s*["\'][0-9a-fA-F]{64}["\']\s*\)',source):
        identifier=int(match[1])
        if identifier in eligible and identifier not in allowed:candidates.setdefault(identifier,None)
    saved_keys=previous.get('keys',{}) if restoring else {}
    for identifier,name in saved_keys.items():
        if not re.fullmatch(r'depotkey_\d+\.yaml-[0-9a-f]{64}',name):raise RuntimeError('Invalid saved depot key record.')
        if int(identifier) in eligible and int(identifier) not in allowed:candidates.setdefault(int(identifier),None)
    if not candidates and not restoring:return {'excluded':[],'changed':False}

    lines=[]
    for line in source.splitlines(keepends=True):
        # Do not execute Lua or silently remove compound expressions.
        match=re.match(r'^\s*(addappid|setManifestid|setmanifestid)\s*\(\s*(\d+)\b',line)
        if match and int(match[2]) in candidates:
            if not re.fullmatch(r'\s*\w+\s*\([^()\n]*\)\s*;?\s*(?:--[^\n]*)?\n?',line):
                raise RuntimeError('LuaMoon script contains a compound depot expression; selection was not changed.')
            continue
        lines.append(line)
    filtered=''.join(lines)
    # Every excluded keyed call must be gone, including unsupported layouts.
    for identifier in candidates:
        if re.search(r'\b(?:addappid|setManifestid|setmanifestid)\s*\(\s*'+str(identifier)+r'\b',filtered):
            raise RuntimeError('LuaMoon script uses an unsupported depot layout; selection was not changed.')
    backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    digest=hashlib.sha256(source.encode()).hexdigest()
    original=backup/(digest+'.lua')
    if not original.exists():original.write_text(source);original.chmod(0o600)
    saved_keys=dict(saved_keys)
    for identifier,path in candidates.items():
        if path is not None:
            saved=backup/(path.name+'-'+hashlib.sha256(path.read_bytes()).hexdigest())
            if not saved.exists():saved.write_bytes(path.read_bytes());saved.chmod(0o600)
            saved_keys[str(identifier)]=saved.name
    for identifier,name in saved_keys.items():
        if int(identifier) in allowed:
            restored=cache/('depotkey_'+identifier+'.yaml')
            if not restored.exists():restored.write_bytes((backup/name).read_bytes());restored.chmod(0o600)
    changed=script.read_text()!=filtered or any(path is not None for path in candidates.values())
    temporary=script.with_suffix('.playlite-tmp')
    temporary.write_text(filtered);temporary.chmod(0o600);temporary.replace(script)
    for path in candidates.values():
        if path is not None:path.unlink()
    temporary=ledger.with_suffix('.tmp')
    temporary.write_text(json.dumps({'original_sha256':digest,'filtered_sha256':hashlib.sha256(filtered.encode()).hexdigest(),'keys':saved_keys}))
    temporary.chmod(0o600);temporary.replace(ledger)
    return {'excluded':sorted(candidates),'changed':changed}
