"""Build a standalone plugin release without the base application."""
import hashlib
import argparse
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED
root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=root / 'dist')
out = parser.parse_args().output
out.mkdir(parents=True,exist_ok=True)
archive = out / 'plugin.zip'
with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    # Include the new required module in working-tree builds before it is staged.
    tracked.append('depot_setup.py')
    for name in sorted(set(filter(None, tracked))):
        relative = Path(name)
        path = root / relative
        if relative.parts[0] == 'tools' and (len(relative.parts) < 2 or relative.parts[1] not in ('steam', 'worker', 'vm', 'build_worker.py', 'appinfo_patch.py', 'preflight_patch.py')):
            continue
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and not any(part in ('.git', '.github', 'dist', '__pycache__', 'tests', '.venv') for part in relative.parts):
            bundle.write(path, str(relative))
with ZipFile(archive) as bundle:
    required = ('manifest.json', 'plugin.py', 'steam_setup.py', 'environment_uninstall.py',
                'depot_setup.py', 'queue_runner.py', 'tools/build_worker.py', 'tools/appinfo_patch.py', 'tools/preflight_patch.py', 'tools/worker/Dockerfile',
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
