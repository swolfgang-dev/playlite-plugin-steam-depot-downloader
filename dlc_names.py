"""Resolve missing DLC titles without guessing from unrelated base-game depots."""
import re
from .app_info import content_depots


def depot_title(label, app):
    label=label.strip()
    label=re.sub(r'^DLC\s+'+str(app)+r'\s*[-:]?\s*','',label,flags=re.I)
    label=re.sub(r'\s*\('+str(app)+r'\)(?:\s+Depot)?$','',label,flags=re.I)
    label=re.sub(r'\s*[-–:]\s*(?:windows|linux|macos|osx)(?:\s+\d+-bit)?$','',label,flags=re.I).strip()
    if not label or re.fullmatch(r'(?:DLC\s*)?\d+',label,re.I) or re.search(r'\bDLC\s*\d+$',label,re.I):return ''
    return label


def resolve_names(info, rows, search):
    requests=0
    for entry in info['dlc']:
        if entry['name'].strip():continue
        title=''
        if requests<8:
            requests+=1
            try:title=search.details(entry['id'])['name'].strip()
            except Exception:pass  # A missing store page must not block downloading.
        if not title:
            related=content_depots(info,entry)
            candidates=[row.name for row in rows if row.id in related]
            candidates.extend(row.get('name','') for row in related.values())
            for candidate in candidates:
                if not isinstance(candidate,str):continue
                title=depot_title(candidate,entry['id'])
                if title:break
        if title:entry['name']=title
    return info
