"""Mirror only Steam's sign-in window; all credentials stay in its own UI."""
import base64
import os
from pathlib import Path
import subprocess

KEYS={'Tab','shift+Tab','Return','BackSpace','Delete','Left','Right','Up','Down','Home','End','ctrl+a','Escape'}


def run(args, **options):
    try:
        result=subprocess.run(args,capture_output=True,timeout=10,**options)
        if result.returncode:raise RuntimeError()
        return result.stdout
    except Exception:
        raise RuntimeError('Steam sign-in display is unavailable. Open the VM desktop and retry.') from None


def view(events, authenticated=False):
    if authenticated:return {'authenticated':True}
    if not isinstance(events,list) or len(events)>32:raise ValueError('Invalid Steam sign-in input.')
    windows=run(['xdotool','search','--onlyvisible','--class','steam']).decode().split()
    window=None
    for candidate in windows:
        if not candidate.isdecimal():continue
        name=run(['xdotool','getwindowname',candidate]).decode(errors='replace').strip().lower()
        if name=='steam' or any(term in name for term in ('steam login','sign in to steam','steam guard')):
            window=candidate;break
    if window is None:raise RuntimeError('Steam sign-in window is not visible yet. Open Steam in the VM and retry.')
    geometry=run(['xdotool','getwindowgeometry','--shell',window]).decode()
    dimensions=dict(line.split('=',1) for line in geometry.splitlines() if '=' in line)
    width,height=int(dimensions['WIDTH']),int(dimensions['HEIGHT'])
    if not 1<=width<=4096 or not 1<=height<=4096:raise RuntimeError('Steam sign-in window has an unsupported size.')
    for event in events:
        if not isinstance(event,dict):raise ValueError('Invalid Steam sign-in input.')
        kind=event.get('kind')
        if kind=='click':
            x,y=event.get('x'),event.get('y')
            if type(x) is not int or type(y) is not int or not (0<=x<width and 0<=y<height):raise ValueError('Invalid Steam sign-in position.')
        elif kind=='text':
            value=event.get('text')
            if not isinstance(value,str) or not 1<=len(value)<=512 or any(c in value for c in '\r\n\0'):raise ValueError('Invalid Steam sign-in text.')
        elif kind=='key':
            if event.get('key') not in KEYS:raise ValueError('Invalid Steam sign-in key.')
        else:raise ValueError('Invalid Steam sign-in input.')
    if events:run(['xdotool','windowactivate','--sync',window])
    for event in events:
        if event['kind']=='click':
            run(['xdotool','mousemove','--window',window,str(event['x']),str(event['y'])]);run(['xdotool','click','1'])
        elif event['kind']=='text':
            # Never put a username, password or Guard code in process arguments.
            run(['xdotool','type','--clearmodifiers','--file','-'],input=event['text'].encode())
        else:run(['xdotool','key','--clearmodifiers',event['key']])
    png=run(['import','-window',window,'png:-'])
    if not png.startswith(b'\x89PNG') or len(png)>4*1024*1024:raise RuntimeError('Steam sign-in capture failed.')
    return {'authenticated':False,'width':width,'height':height,'image':base64.b64encode(png).decode()}
