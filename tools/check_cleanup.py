"""Verify crash cleanup against disposable Docker containers and dummy secrets."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('downloader', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules['downloader'] = module
spec.loader.exec_module(module)
from downloader.network import Network, IMAGE, LABEL, WORKER_LABEL
from downloader.guardian import process_token


def command(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.DEVNULL).strip()


def main():
    name = 'playlite-cleanup-test-' + uuid.uuid4().hex[:8]
    directory = tempfile.mkdtemp(prefix='playlite-vpn-')
    Path(directory, 'openvpn_password').write_text('dummy-test-secret')
    parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    monitor = None
    gateway = worker = None
    try:
        gateway = command('run', '-d', '--name', name, '--label', LABEL + '=' + str(os.getuid()),
                          '--network', 'bridge', '--entrypoint', '/bin/sh', IMAGE, '-c', 'sleep 120')
        worker = command('run', '-d', '--name', name + '-worker', '--network', 'container:' + gateway,
                         '--label', WORKER_LABEL + '=' + gateway, '--entrypoint', '/bin/sh', IMAGE, '-c', 'sleep 120')
        monitor = subprocess.Popen([sys.executable, str(root / 'guardian.py'), str(parent.pid),
                                    process_token(parent.pid), gateway, directory])
        parent.kill()
        parent.wait()
        monitor.wait(timeout=15)
        for cid in [worker, gateway]:
            if subprocess.run(['docker', 'inspect', cid], capture_output=True).returncode == 0:
                raise RuntimeError('A container survived parent crash cleanup.')
        if Path(directory).exists():
            raise RuntimeError('Temporary secrets survived parent crash cleanup.')
        print('PASS: parent crash removes worker, gateway, and temporary secrets')
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if monitor and monitor.poll() is None:
            monitor.kill()
            monitor.wait()
        for cid in [worker, gateway]:
            if cid: subprocess.run(['docker', 'rm', '-f', cid], capture_output=True)
        if Path(directory).exists():
            import shutil
            shutil.rmtree(directory)


if __name__ == '__main__':
    main()
