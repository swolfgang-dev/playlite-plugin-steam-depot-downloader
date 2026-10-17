"""Install Moon from the pinned recipe and attach Playlite's bounded LuaMoon mailbox."""
import os
import json
import re
import stat
import tempfile
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
        control = Path(control)
        if not control.is_absolute() or any(ord(character) < 32 for character in str(control)):
            raise ValueError('The Moon control directory must be an absolute path without control characters.')
        bridge = re.sub(r"'/control/([^']+)'",
                        lambda match: json.dumps(str(control / match[1]), ensure_ascii=False), bridge)
    updated = source.rstrip() + '\n\n' + MARKER + '\n' + bridge
    if main.read_text() != updated:
        descriptor, name = tempfile.mkstemp(prefix='.playlite-bridge-', dir=main.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(updated)
                stream.flush()
                os.fchmod(stream.fileno(), stat.S_IMODE(main.stat().st_mode))
                os.fsync(stream.fileno())
            temporary.replace(main)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    if '--bridge-only' not in sys.argv:
        result = subprocess.run(['bash', str(ASSETS / 'moon-install.sh'), '--nolaunch'], stdin=subprocess.DEVNULL)
        if result.returncode:
            raise SystemExit(result.returncode)
    install_bridge(control=os.environ.get('PLAYLITE_CONTROL'))
