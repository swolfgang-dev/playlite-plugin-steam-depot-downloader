"""Install pinned Moon and attach Playlite's bounded LuaMoon mailbox."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ASSETS = Path(__file__).resolve().parent
MARKER = '-- Playlite private control bridge'


def install_bridge(home=None, control=None):
    home = Path(home or os.environ['HOME'])
    main = home / '.local/share/Lumen/luatools/backend/main.lua'
    source = main.read_text()
    if MARKER in source:
        source = source[:source.index(MARKER)]
    else:
        offset = source.rfind('\nreturn {')
        if offset < 0:
            raise RuntimeError('LuaMoon lifecycle contract changed; bridge was not installed.')
        backup = main.with_suffix('.lua.before-playlite')
        if not backup.exists():
            shutil.copy2(main, backup)
        source = source[:offset] + source[offset:].replace('\nreturn {', '\nlocal lifecycle = {', 1)
    bridge = (ASSETS / 'moon_bridge.lua').read_text()
    if control is not None:
        bridge = bridge.replace('/control/', str(Path(control)) + '/')
    updated = source + '\n' + MARKER + '\n' + bridge
    if main.read_text() != updated:
        temporary = main.with_suffix('.playlite-tmp')
        temporary.write_text(updated)
        temporary.replace(main)


if __name__ == '__main__':
    if '--bridge-only' not in sys.argv:
        result = subprocess.run(['bash', str(ASSETS / 'moon-install.sh'), '--nolaunch'], stdin=subprocess.DEVNULL)
        if result.returncode:
            raise SystemExit(result.returncode)
    install_bridge(control=os.environ.get('PLAYLITE_CONTROL'))
