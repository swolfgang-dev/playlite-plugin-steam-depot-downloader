"""Build a single launchable script containing the credential-free VM recipe."""
import base64
import hashlib
import io
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parent.parent
assets = root / 'tools/vm'
output = root / 'dist/install-steam-vm.sh'
output.parent.mkdir(exist_ok=True)
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
    import importlib.util
    spec = importlib.util.spec_from_file_location('installer_assets', assets / 'install.py')
    installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
    for name in installer.ASSET_FILES:
        source = installer.asset_path(name)
        entry = tarfile.TarInfo(name)
        payload = source.read_bytes()
        entry.size = len(payload)
        entry.mode = 0o755 if source.suffix in ('.py', '.sh') else 0o644
        archive.addfile(entry, io.BytesIO(payload))
payload = buffer.getvalue()
encoded = base64.b64encode(payload).decode()
script = '''#!/usr/bin/env bash
set -euo pipefail
exec python3 - "$@" <<'PLAYLITE_VM_INSTALLER'
import base64, hashlib, io, pathlib, subprocess, sys, tarfile, tempfile
payload = base64.b64decode("ENCODED_PAYLOAD")
if hashlib.sha256(payload).hexdigest() != "PAYLOAD_SHA256":
    raise SystemExit('Embedded installer checksum mismatch')
with tempfile.TemporaryDirectory(prefix='playlite-steam-vm-installer-') as folder:
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        archive.extractall(folder, filter='data')
    result = subprocess.run([sys.executable, str(pathlib.Path(folder) / 'install.py'), *sys.argv[1:]])
    raise SystemExit(result.returncode)
PLAYLITE_VM_INSTALLER
'''.replace('ENCODED_PAYLOAD', encoded).replace('PAYLOAD_SHA256', hashlib.sha256(payload).hexdigest())
output.write_text(script)
output.chmod(0o755)
(root / 'dist/VM-SHA256SUMS').write_text(hashlib.sha256(output.read_bytes()).hexdigest() + '  install-steam-vm.sh\n')
print(output)
