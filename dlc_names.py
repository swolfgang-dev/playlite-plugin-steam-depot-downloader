"""General, cached DLC name resolution; names do not imply download availability."""
import re
import json
import time
from PyQt6.QtCore import QSettings
from .app_info import content_depots


class NameCache:
    def __init__(self):self.settings=QSettings('Playlite','SteamDownloader')
    def read(self, app):
        try:
            item=json.loads(self.settings.value(f'dlcNames/{app}','null'))
            if not isinstance(item,dict):return None
            age=time.time()-item['time'];ttl=30*86400 if item.get('name') else 3600
            if 0<=age<ttl and isinstance(item.get('name'),str):return item['name']
        except (ValueError,TypeError,KeyError):pass
        return None
    def save(self,app,name):
        self.settings.setValue(f'dlcNames/{app}',json.dumps({'name':name,'time':time.time()}))


def depot_title(label, app):
    label=label.strip()
    label=re.sub(r'^DLC\s+'+str(app)+r'\s*[-:]?\s*','',label,flags=re.I)
    label=re.sub(r'\s*\('+str(app)+r'\)(?:\s+Depot)?$','',label,flags=re.I)
    label=re.sub(r'\s*[-–:]\s*(?:windows|linux|macos|osx)(?:\s+\d+-bit)?$','',label,flags=re.I).strip()
    if not label or re.fullmatch(r'(?:DLC\s*)?\d+',label,re.I) or re.search(r'\bDLC\s*\d+$',label,re.I):return ''
    return label


def resolve_names(info, rows, search, cache=None):
    cache=cache if cache is not None else NameCache();requests=0
    for entry in info['dlc']:
        if entry['name'].strip():
            cache.save(entry['id'],entry['name']);continue
        cached=cache.read(entry['id']);title=cached or '';attempted=False
        if cached is None and requests<8:
            requests+=1;attempted=True
            try:
                candidate=search.details(entry['id'])['name']
                if isinstance(candidate,str):title=candidate.strip()
            except Exception:pass
        if not title:
            related=content_depots(info,entry)
            candidates=[row.name for row in rows if row.id in related]
            candidates.extend(row.get('name','') for row in related.values())
            for candidate in candidates:
                if isinstance(candidate,str):title=depot_title(candidate,entry['id'])
                if title:break
        if not title and cached is None and requests<8:
            requests+=1;attempted=True
            try:
                candidate=search.secondary_name(entry['id'])
                if isinstance(candidate,str):title=candidate.strip()
            except Exception:pass
        if title:entry['name']=title;cache.save(entry['id'],title)
        elif cached is None and attempted:cache.save(entry['id'],'')
    return info
