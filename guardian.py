"""Remove this session's network resources if its owning Playlite process exits."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
if __package__:
    from .constants import IMAGE, LABEL, WORKER_LABEL
else:
    from constants import IMAGE, LABEL, WORKER_LABEL


def process_token(pid):
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rpartition(')')[2].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def safe_directory(directory):
    path = Path(directory)
    try:
        info = path.lstat()
        return (path.name.startswith('playlite-vpn-') and stat.S_ISDIR(info.st_mode)
                and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    except OSError:
        return False


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=15)


def cleanup(container, directory):
    if not safe_directory(directory):
        return False
    result = docker('inspect', container)
    if result.returncode:
        # A missing container is safe; an unreachable daemon needs another try.
        if docker('info', '--format', '{{.ID}}').returncode:
            return False
        if 'No such' not in result.stderr:
            return False
    else:
        data = json.loads(result.stdout)[0]
        if (data['Id'] != container or data['Config'].get('Labels', {}).get(LABEL) != str(os.getuid())
                or data['Config']['Image'] != IMAGE):
            return False
        workers = docker('ps', '-aq', '--filter', 'label=' + WORKER_LABEL + '=' + container)
        if workers.returncode:
            return False
        for worker in workers.stdout.split():
            if docker('rm', '-f', worker).returncode:
                return False
        if docker('rm', '-f', container).returncode:
            return False
    shutil.rmtree(directory)
    return True


def main(pid, token, container, directory):
    while safe_directory(directory) and process_token(pid) == token:
        time.sleep(1)
    while safe_directory(directory):
        try:
            if cleanup(container, directory):
                return
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(5)


if __name__ == '__main__':
    main(*sys.argv[1:])
