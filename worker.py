"""Experimental isolated native worker. No UI download action is enabled yet."""
from dataclasses import dataclass
from pathlib import Path
from .constants import IMAGE

@dataclass(frozen=True)
class Request:
    app: int
    depot: int
    manifest: int | None = None
    metadata_only: bool = False

    def arguments(self):
        for identifier in (self.app, self.depot):
            if type(identifier) is not int or not 0 < identifier < 2**32:
                raise ValueError('App and depot IDs must be positive 32-bit integers.')
        args = ['-app', str(self.app), '-depot', str(self.depot), '-dir', '/output', '-validate']
        if self.manifest is not None:
            if type(self.manifest) is not int or not 0 < self.manifest < 2**64:
                raise ValueError('Manifest ID must be a positive 64-bit integer.')
            args += ['-manifest', str(self.manifest)]
        if self.metadata_only:
            args.append('-manifest-only')
        return args


def worker_args(network, request, staging, image='playlite-depot-worker:test', pack=None, username=None, qr=False):
    """Only mount caller-created staging and optional read-only pack inputs."""
    arguments = request.arguments()
    if username and qr:
        raise ValueError('Choose Steam username login or QR login.')
    if username is not None:
        if not username.strip() or any(c in username for c in '\r\n\0'):
            raise ValueError('Enter a valid Steam account name.')
        arguments += ['-username', username.strip()]
    elif qr:
        arguments.append('-qr')
    if username or qr:
        import secrets
        arguments += ['-loginid', str(secrets.randbelow(2**32 - 1) + 1)]
    staging = Path(staging).resolve(strict=True)
    if not staging.is_dir():
        raise ValueError('Create a private test staging directory first.')
    mounts = ['--mount', f'type=bind,src={staging},dst=/output',
              '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
              '-e', 'HOME=/tmp', '-e', 'XDG_DATA_HOME=/tmp/.local/share',
              '-e', 'DOTNET_PROCESSOR_COUNT=2', '--workdir', '/tmp']
    if pack is not None:
        pack = Path(pack).resolve(strict=True)
        if not pack.is_dir():raise ValueError('Prepared pack must be a directory.')
        mounts += ['--mount', f'type=bind,src={pack},dst=/input,readonly']
        if (pack/'manifest.bin').is_file():arguments+=['-manifestfile','/input/manifest.bin']
        if (pack / 'depot.keys').is_file():
            arguments += ['-depotkeys', '/input/depot.keys']
    network.check()  # Fail closed before starting any worker.
    args = network.probe_args('')
    if username or qr:
        args.insert(1, '-i')
    args[args.index('64m')] = '512m'
    args[args.index('32')] = '128'
    args[args.index('--entrypoint') + 1] = '/tool/DepotDownloaderMod'
    index = args.index(IMAGE)
    args[index] = image
    args[index + 1:] = arguments
    args[index:index] = mounts
    return args


def require_download_success(returncode, output):
    """The upstream program can return zero after failing every depot."""
    if returncode or 'No valid depot key' in output or 'result: AccessDenied' in output:
        raise RuntimeError('The depot download failed. A manifest pack or Steam authentication may be required.')
    import re
    totals = re.findall(r'Total downloaded:.*?from (\d+) depots', output)
    if not totals or int(totals[-1]) == 0:
        raise RuntimeError('No depot was downloaded; exit code zero alone is not success.')
