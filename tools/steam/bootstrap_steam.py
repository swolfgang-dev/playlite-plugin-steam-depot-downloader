"""Install Valve's verified bootstrap without the distro launcher prompt."""
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

VERSION='1.0.0.79'
SHA256='3a0012daf5889311c2e1dcf33a587b409019b66d893a52192df6947e18f1ec46'
URL=f'https://repo.steampowered.com/steam/archive/beta/steam_{VERSION}.tar.gz'


def install(home=None):
    home=Path(home or Path.home())
    root=home/'.steam/debian-installation'
    required=('steam.sh','ubuntu12_32/steam','ubuntu12_32/steam-runtime/run.sh','ubuntu12_32/steam-runtime/setup.sh')
    if all((root/name).is_file() for name in required):return
    with urllib.request.urlopen(URL,timeout=60) as response:
        data=response.read(64*1024*1024+1)
    if hashlib.sha256(data).hexdigest()!=SHA256:raise RuntimeError('Steam bootstrap checksum verification failed.')
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
        source=archive.extractfile('steam-launcher/bootstraplinux_ubuntu12_32.tar.xz')
        if source is None:raise RuntimeError('Steam bootstrap archive is incomplete.')
        payload=source.read()
    root.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:xz') as archive:
        archive.extractall(root,filter='data')
    for name in ('steam','root'):
        link=home/'.steam'/name
        if not link.exists() and not link.is_symlink():link.symlink_to(root)
    marker=root/'deb-installer/version';marker.parent.mkdir(exist_ok=True);marker.write_text(VERSION)

if __name__=='__main__':install()
