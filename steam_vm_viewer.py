"""Launch an owned VM viewer that can close without stopping the guest."""
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit
from .vm_backend import installer_module


def open_viewer(network,progress):
    installer=installer_module()
    binary=shutil.which('remote-viewer')
    if binary is None:
        folder=network.profile/'tools/steam-viewer'
        binary=str(folder/'unpacked/usr/bin/remote-viewer')
        if not Path(binary).is_file():
            progress('Preparing the Steam VM sign-in viewer…')
            folder.mkdir(parents=True,exist_ok=True)
            installer.execute(['apt-get','download','virt-viewer'],cwd=folder)
            packages=list(folder.glob('virt-viewer_*.deb'))
            if len(packages)!=1:raise RuntimeError('Could not prepare the VM viewer. Install virt-viewer and retry.')
            installer.execute(['dpkg-deb','-x',packages[0],folder/'unpacked'])
    address=installer.execute(['virsh','-c','qemu:///session','domdisplay',network.cfg['domain']]).stdout.strip()
    parsed=urlsplit(address)
    if parsed.scheme!='spice' or parsed.hostname!='127.0.0.1' or not parsed.port:
        raise RuntimeError('The Steam VM does not have a local SPICE viewer endpoint.')
    return subprocess.Popen([binary,'--title=Steam sign-in — Playlite',address],env=installer.host_environment(),
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
