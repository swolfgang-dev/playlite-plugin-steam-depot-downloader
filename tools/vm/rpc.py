"""QEMU guest-agent RPC client; only a bounded request reaches the VM daemon."""
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import uuid


def main():
    if os.getuid() == 0:
        os.execv('/usr/sbin/runuser', ['runuser', '-u', 'ubuntu', '--', '/usr/bin/python3',
                                     str(Path(__file__).resolve()), *sys.argv[1:]])
    os.umask(0o077)
    replies = Path.home() / '.local/state/playlite-vm/replies'
    replies.mkdir(parents=True, exist_ok=True, mode=0o700)
    if len(sys.argv) == 3 and sys.argv[1] == '--remove-reply':
        if not re.fullmatch('[0-9a-f]{32}', sys.argv[2]):
            raise ValueError('Invalid reply identity.')
        (replies / (sys.argv[2] + '.json')).unlink(missing_ok=True)
        return
    if len(sys.argv) != 1:
        raise ValueError('Invalid RPC arguments.')
    source = sys.stdin.buffer.read(131073)
    if len(source) > 131072:
        raise ValueError('RPC request exceeds limit.')
    request = json.loads(source)
    if not isinstance(request, dict):
        raise ValueError('Invalid RPC request.')
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(180)
        connection.connect(str(Path.home() / '.local/state/playlite-vm/control/runtime.sock'))
        connection.sendall(json.dumps(request).encode() + b'\n')
        # Stream to a private guest file rather than QGA's bounded stdout capture.
        identifier = uuid.uuid4().hex
        output = replies / (identifier + '.json')
        try:
            count = 0
            with output.open('xb') as stream:
                while True:
                    data = connection.recv(262144)
                    if not data:
                        raise RuntimeError('Incomplete VM response.')
                    count += len(data)
                    if count > 96 * 1024 * 1024:
                        raise ValueError('VM response exceeds limit.')
                    stream.write(data)
                    if data.endswith(b'\n'):
                        break
            if count <= 32768:
                print(output.read_text(), end='')
                output.unlink()
            else:
                print(json.dumps({'reply': identifier}))
        except Exception:
            output.unlink(missing_ok=True)
            raise
    for old in replies.glob('*.json'):
        if old.stat().st_mtime < time.time() - 3600:
            old.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'ok': False, 'error': 'VM control is unavailable. Run Steam setup or inspect its service log.'}))
        sys.exit(1)
