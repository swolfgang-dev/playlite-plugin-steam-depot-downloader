"""Install upstream user-local Moon, then add a bounded App-ID-only interface."""
import os
from pathlib import Path
import subprocess

result=subprocess.run(['bash','/opt/moon-install.sh','--nolaunch'],stdin=subprocess.DEVNULL)
if result.returncode:raise SystemExit(result.returncode)
main=Path(os.environ['HOME'])/'.local/share/Lumen/luatools/backend/main.lua'
source=main.read_text()
marker='-- Playlite private control bridge'
if marker not in source:
    offset=source.rfind('\nreturn {')
    if offset<0:raise SystemExit('LuaMoon lifecycle contract changed; private bridge was not installed.')
    source=source[:offset]+source[offset:].replace('\nreturn {','\nlocal lifecycle = {',1)
    source+='\n'+marker+'\n'+Path('/opt/moon_bridge.lua').read_text()
    temporary=main.with_suffix('.playlite-tmp');temporary.write_text(source);temporary.replace(main)
