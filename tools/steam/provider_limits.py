"""Capture only provider quota fields from Moon's existing HTTP responses."""
import json
import re
import sys
import time
import shlex
from pathlib import Path

FIELDS=('daily_usage','daily_limit','remaining','limit','used','retry_after','reset')
HEADERS={'retry-after':'retry_after','x-ratelimit-limit':'limit','ratelimit-limit':'limit',
         'x-ratelimit-remaining':'remaining','ratelimit-remaining':'remaining',
         'x-ratelimit-reset':'reset','ratelimit-reset':'reset'}


def parse_limits(provider,status,headers,body):
    result={'provider':re.sub(r'[^\w .-]','',provider)[:80] or 'Unknown provider',
            'http_status':int(status),'observed_at':int(time.time())}
    for line in headers.splitlines():
        key,separator,value=line.partition(':')
        field=HEADERS.get(key.strip().lower())
        if separator and field:
            value=value.strip()
            if value.isdecimal() and len(value)<16:result[field]=int(value)
            elif field=='retry_after' and re.fullmatch(r'[A-Za-z0-9 ,:-]{1,80}',value):result[field]=value
    if len(body)<=65536:
        try:data=json.loads(body)
        except (ValueError,TypeError):data={}
        if isinstance(data,dict):
            for field in FIELDS:
                value=data.get(field)
                if isinstance(value,int) and not isinstance(value,bool) and 0<=value<10**15:
                    result[field]=value
    if 'daily_usage' in result and 'daily_limit' in result:
        result['daily_remaining']=max(0,result['daily_limit']-result['daily_usage'])
    return result


def install_capture(backend):
    script=backend/'scripts/smart_download.sh'
    source=script.read_text()
    marker='# Playlite provider quota capture'
    if marker in source:return
    anchor='    limit_reason="$(source_limit_reason "${http:-0}" "${C_ZIP[i]}")"'
    header_anchor='    elif [[ "${clean,,}" =~ ^\\<\\ content-length:'
    if anchor not in source or header_anchor not in source:
        return  # Upstream changed: downloads still work, diagnostics unavailable.
    source=source.replace(header_anchor,'''    elif [[ "${clean,,}" =~ ^\\<\\ (retry-after|x-ratelimit-(limit|remaining|reset)|ratelimit-(limit|remaining|reset)): ]]; then
      printf '%s\\n' "${clean#< }" >> "$header_file"
'''+header_anchor,1)
    source=source.replace(anchor,'''    # Playlite provider quota capture
    python3 PLAYLITE_CAPTURE_SCRIPT "$STATE_FILE" "${C_NAME[i]}" "${http:-0}" "${C_HEAD[i]}" "${C_ZIP[i]}" || true
'''+anchor,1)
    source=source.replace('PLAYLITE_CAPTURE_SCRIPT',shlex.quote(str(Path(__file__).resolve())))
    temporary=script.with_suffix('.playlite-tmp');temporary.write_text(source);temporary.chmod(script.stat().st_mode);temporary.replace(script)


def read_limits(backend,appid):
    path=backend/'temp_dl'/f'{appid}_state.json.limits.jsonl'
    if not path.is_file() or path.stat().st_size>65536:return []
    rows=[]
    for line in path.read_text().splitlines()[-64:]:
        try:row=json.loads(line)
        except ValueError:continue
        if isinstance(row,dict):rows.append(row)
    return rows


if __name__=='__main__':
    state,provider,status,header_path,body_path=sys.argv[1:]
    headers=Path(header_path).read_text()[:65536]
    with Path(body_path).open('rb') as stream:body=stream.read(65537).decode('utf-8',errors='replace')
    row=parse_limits(provider,status,headers,body)
    with Path(state+'.limits.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
