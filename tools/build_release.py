"""Build a standalone plugin release without the base application."""
import hashlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root = Path(__file__).resolve().parent.parent
out = root / 'dist'
out.mkdir(exist_ok=True)
archive = out / 'plugin.zip'
with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if relative.parts[0] == 'tools' and (len(relative.parts) < 2 or relative.parts[1] not in ('steam', 'worker', 'vm')):
            continue
        if path.is_file() and not path.is_symlink() and not any(part in ('.git', '.github', 'dist', '__pycache__', 'tests', '.venv') for part in relative.parts):
            bundle.write(path, str(relative))
with ZipFile(archive) as bundle:
    required = ('manifest.json', 'plugin.py', 'steam_setup.py', 'environment_uninstall.py',
                'tools/steam/Dockerfile', 'tools/steam/bridge.py', 'tools/steam/seccomp.json',
                'tools/steam/setup_moon.py', 'tools/steam/moon_bridge.lua',
                'tools/steam/steam_control.lua', 'tools/steam/retail_selection.py',
                'tools/steam/provider_limits.py', 'vm_backend.py', 'vm_setup.py',
                'tools/vm/install.py', 'tools/vm/runtime_server.py', 'tools/vm/rpc.py',
                'tools/vm/setup-runtime.py', 'tools/steam/moon-install.sh', 'tools/steam/MOON-LICENSE', 'tools/steam/NOTICE', 'tools/steam/MOBY-LICENSE')
    for name in required:
        assert name in bundle.namelist(), 'Required runtime file missing: ' + name
(out / 'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  plugin.zip\n')
print(archive)
