#!/usr/bin/env python3
"""Create a credential-free, private Steam VM. Run as the desktop user."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import stat
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET

OWNER = 'SteamDepotDownloader/steam-vm/v1'
ASSETS = Path(__file__).resolve().parent
IMAGE = 'noble-server-cloudimg-amd64.img'
IMAGE_URL = 'https://cloud-images.ubuntu.com/noble/current/'
ASSET_FILES = ('install.py', 'guest_control.py', 'prepare-storage.py',
               'provision-guest.sh', 'install-nordvpn.sh', 'install.sh', 'README.md', 'runtime_server.py', 'rpc.py', 'steam_sign_in.py',
               'setup-runtime.py', 'steam/bridge.py', 'steam/bootstrap_steam.py', 'steam/setup_moon.py', 'steam/moon_bridge.lua',
               'steam/steam_control.lua', 'steam/retail_selection.py', 'steam/provider_limits.py',
               'steam/moon-install.sh', 'steam/MOON-LICENSE')


def default_root():
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    return data / 'playlite/plugin-data/SteamDepotDownloader/steam-vm'


def host_environment():
    """Keep libvirt and desktop services outside Playlite's private profile."""
    import pwd
    environment = os.environ.copy()
    if environment.get('PLAYLITE_PROFILE') == 'repo':
        environment['HOME'] = environment.get('PLAYLITE_HOST_HOME') or pwd.getpwuid(os.getuid()).pw_dir
        for key in ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
            value = environment.get('PLAYLITE_HOST_' + key)
            if value:
                environment[key] = value
            else:
                environment.pop(key,None)
    return environment


def execute(args, **options):
    options.setdefault('capture_output', True)
    options.setdefault('text', True)
    options.setdefault('env', host_environment())
    try:
        return subprocess.run([str(arg) for arg in args], check=True, **options)
    except subprocess.CalledProcessError as error:
        output = '\n'.join(str(value).strip() for value in (error.stdout, error.stderr) if value)
        raise RuntimeError(f'{args[0]} failed (exit {error.returncode}).\n{output or "No command output was returned."}') from None


def register_service(cfg):
    # Register with the real user manager even when the app has a private HOME.
    root = Path(cfg['root'])
    service = root / (cfg['service'] + '.service')
    service.write_text((root / 'virtiofs.service').read_text())
    execute(['systemctl', '--user', 'link', '--force', service])
    execute(['systemctl', '--user', 'daemon-reload'])


def subordinate_id(filename, identity):
    for line in Path(filename).read_text().splitlines():
        name, start, count = line.split(':')
        if name in (identity, str(os.getuid())) and int(count) >= 65536:
            return int(start)
    raise RuntimeError(f'{filename} needs a range of at least 65536 IDs for {identity}.')


def configuration(root, shared, name=None):
    root = Path(root).expanduser().resolve()
    shared = Path(shared).expanduser().resolve()
    if not shared.is_dir():
        raise ValueError('The shared games folder must already exist.')
    if any(char in str(root) + str(shared) for char in '\r\n\0'):
        raise ValueError('VM and shared-folder paths cannot contain control characters.')
    if root == shared or root.is_relative_to(shared) or shared.is_relative_to(root):
        raise ValueError('VM userdata and shared game files must be separate folders.')
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    domain = name or 'playlite-steam-' + digest
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,62}', domain):
        raise ValueError('Use a VM name containing letters, numbers, dashes, or underscores.')
    return {'owner': OWNER, 'uid': os.getuid(), 'gid': os.getgid(),
            'root': str(root), 'shared': str(shared), 'domain': domain,
            'service': 'playlite-steam-virtiofs-' + digest,
            'socket': f'/run/user/{os.getuid()}/libvirt/qemu/run/steam-{digest}.sock',
            'memory_mib': 8192, 'cpus': 4, 'disk_gib': 160,
            'staging': '.steam-vm-' + digest, 'shared_mount': shared_mount(shared)}


def shared_mount(shared):
    result = execute(['findmnt', '--json', '-T', shared, '-o', 'TARGET,FSTYPE'],
                     capture_output=True, text=True)
    mount = json.loads(result.stdout)['filesystems'][0]
    return {'target': mount['target'], 'fstype': mount['fstype']}


def load(root):
    cfg = json.loads((Path(root) / 'vm.json').read_text())
    if cfg.get('owner') != OWNER or cfg.get('uid') != os.getuid():
        raise RuntimeError('This VM does not belong to the current user.')
    if Path(cfg['root']).resolve() != Path(root).resolve():
        raise RuntimeError('VM userdata moved; update the configuration before starting it.')
    return cfg


def asset_path(name):
    candidate = ASSETS / name
    if candidate.exists():
        return candidate
    # Repository builds share the Steam bridge with the legacy Docker backend.
    if name.startswith('steam/'):
        return ASSETS.parent / name
    return candidate


def cloud_config(cfg):
    scripts = [('guest_control.py', '/usr/local/bin/playlite-vm-control', '0755'),
               ('prepare-storage.py', '/usr/local/lib/playlite-vm/prepare-storage.py', '0755'),
               ('provision-guest.sh', '/usr/local/lib/playlite-vm/provision-guest.sh', '0755')]
    files = [{'path': target, 'permissions': mode, 'content': (ASSETS / source).read_text()}
             for source, target, mode in scripts]
    for name in ASSET_FILES:
        if name in ('runtime_server.py', 'rpc.py', 'steam_sign_in.py', 'setup-runtime.py', 'guest_control.py') or name.startswith('steam/'):
            files.append({'path': '/usr/local/lib/playlite-vm/' + name, 'permissions': '0644',
                          'content': asset_path(name).read_text()})
    files += [{'path': '/etc/playlite-vm.json', 'permissions': '0644',
               'content': json.dumps({'staging': cfg['staging']})}]
    return {'hostname': cfg['domain'], 'manage_etc_hosts': True,
            'growpart': {'mode': 'auto', 'devices': ['/'], 'ignore_growroot_disabled': False},
            'resize_rootfs': True, 'ssh_pwauth': False,
            'users': [{'name': 'ubuntu', 'shell': '/bin/bash', 'lock_passwd': True,
                       'groups': ['adm', 'sudo'], 'sudo': 'ALL=(ALL) NOPASSWD:ALL'}],
            'packages': ['qemu-guest-agent', 'curl', 'software-properties-common'],
            'write_files': files,
            'mounts': [['standalone-games', '/mnt/standalone', 'virtiofs', 'defaults,nofail', '0', '0']],
            'runcmd': [['bash', '/usr/local/lib/playlite-vm/provision-guest.sh']]}


def domain_xml(cfg):
    root = Path(cfg['root'])
    domain = ET.Element('domain', type='kvm')
    ET.SubElement(domain, 'name').text = cfg['domain']
    ET.SubElement(domain, 'title').text = 'Playlite Steam VM'
    ET.SubElement(domain, 'memory', unit='MiB').text = str(cfg['memory_mib'])
    ET.SubElement(domain, 'vcpu').text = str(cfg['cpus'])
    backing = ET.SubElement(domain, 'memoryBacking')
    ET.SubElement(backing, 'source', type='memfd')
    ET.SubElement(backing, 'access', mode='shared')
    boot = ET.SubElement(domain, 'os')
    ET.SubElement(boot, 'type', arch='x86_64', machine='q35').text = 'hvm'
    ET.SubElement(boot, 'boot', dev='hd')
    features = ET.SubElement(domain, 'features')
    ET.SubElement(features, 'acpi'); ET.SubElement(features, 'apic')
    ET.SubElement(domain, 'cpu', mode='host-passthrough')
    ET.SubElement(domain, 'on_poweroff').text = 'destroy'
    ET.SubElement(domain, 'on_reboot').text = 'restart'
    devices = ET.SubElement(domain, 'devices')
    disk = ET.SubElement(devices, 'disk', type='file', device='disk')
    ET.SubElement(disk, 'driver', name='qemu', type='qcow2')
    ET.SubElement(disk, 'source', file=str(root / 'disk.qcow2'))
    # Explicit backingStore is required; the original empty boot exposed this.
    store = ET.SubElement(disk, 'backingStore', type='file')
    ET.SubElement(store, 'format', type='qcow2')
    ET.SubElement(store, 'source', file=str(root / 'ubuntu-base.qcow2'))
    ET.SubElement(store, 'backingStore')
    ET.SubElement(disk, 'target', dev='vda', bus='virtio')
    cd = ET.SubElement(devices, 'disk', type='file', device='cdrom')
    ET.SubElement(cd, 'driver', name='qemu', type='raw')
    ET.SubElement(cd, 'source', file=str(root / 'seed.iso'))
    ET.SubElement(cd, 'target', dev='sda', bus='sata'); ET.SubElement(cd, 'readonly')
    ET.SubElement(devices, 'controller', type='usb', model='qemu-xhci')
    ET.SubElement(devices, 'controller', type='virtio-serial')
    network = ET.SubElement(devices, 'interface', type='user')
    ET.SubElement(network, 'model', type='virtio')
    share = ET.SubElement(devices, 'filesystem', type='mount', accessmode='passthrough')
    ET.SubElement(share, 'driver', type='virtiofs', queue='1024')
    ET.SubElement(share, 'source', socket=cfg['socket'])
    ET.SubElement(share, 'target', dir='standalone-games')
    for kind, name in [('unix', 'org.qemu.guest_agent.0'), ('spicevmc', 'com.redhat.spice.0')]:
        channel = ET.SubElement(devices, 'channel', type=kind)
        ET.SubElement(channel, 'target', type='virtio', name=name)
    graphics = ET.SubElement(devices, 'graphics', type='spice', autoport='yes', listen='127.0.0.1')
    ET.SubElement(graphics, 'listen', type='address', address='127.0.0.1')
    ET.SubElement(graphics, 'clipboard', copypaste='yes')
    ET.SubElement(graphics, 'filetransfer', enable='no')
    video = ET.SubElement(devices, 'video')
    ET.SubElement(video, 'model', type='virtio', heads='1', primary='yes')
    ET.SubElement(devices, 'input', type='tablet', bus='usb')
    ET.SubElement(devices, 'sound', model='ich9')
    ET.SubElement(devices, 'audio', id='1', type='spice')
    ET.indent(domain)
    return ET.tostring(domain, encoding='unicode')


def service_text(cfg, binary, subuid, subgid):
    # virtiofsd's namespace root must map to the host owner so mergerfs can
    # remove private directories. Translate the guest desktop UID to that root.
    args = [str(binary), '--shared-dir', cfg['shared'], '--socket-path', cfg['socket'],
            '--thread-pool-size', '16', '--cache', 'auto', '--xattr',
            '--uid-map', f':0:{cfg["uid"]}:1:', '--uid-map', f':1:{subuid}:65536:',
            '--gid-map', f':0:{cfg["gid"]}:1:', '--gid-map', f':1:{subgid}:65536:',
            '--translate-uid', 'map:1000:0:1', '--translate-gid', 'map:1000:0:1']
    # systemd argument quoting differs from shell quoting; suppress % expansion.
    quoted = [json.dumps(arg.replace('%', '%%').replace('$', '$$')) for arg in args]
    return ('[Unit]\nDescription=Playlite Steam VM shared games\n\n[Service]\n'
            'Type=simple\nExecStartPre=/usr/bin/mkdir -p %t/libvirt/qemu/run\n'
            'ExecStart=' + ' '.join(quoted) + '\nRestart=always\nRestartSec=2\n\n'
            '[Install]\nWantedBy=default.target\n')


def download(url, destination):
    partial = destination.with_suffix(destination.suffix + '.part')
    print('Downloading ' + url, flush=True)
    with urllib.request.urlopen(url, timeout=90) as response, partial.open('wb') as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    partial.replace(destination)


def ensure_daemon(root):
    for binary in (Path('/usr/libexec/virtiofsd'), Path('/usr/lib/qemu/virtiofsd')):
        if binary.is_file():
            return binary
    cache = root / 'tools/virtiofsd'
    binary = cache / 'unpacked/usr/libexec/virtiofsd'
    if not binary.exists():
        cache.mkdir(parents=True, exist_ok=True)
        execute(['apt', 'download', 'virtiofsd'], cwd=cache)
        packages = sorted(cache.glob('virtiofsd_*.deb'))
        if len(packages) != 1:
            raise RuntimeError('Expected one virtiofsd package in the private cache.')
        execute(['dpkg-deb', '-x', packages[0], cache / 'unpacked'])
    execute([binary, '--version'])
    return binary


def write_launcher(root):
    # The copied installer and its assets form a self-contained recreation kit.
    for name in ASSET_FILES:
        source = asset_path(name)
        target = root / 'installer' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
    launcher = root / 'open-vm.sh'
    launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                        'vm_root=$(cd -- "$(dirname -- "$0")" && pwd)\n'
                        'exec python3 "$vm_root/installer/install.py" open --root "$vm_root"\n')
    launcher.chmod(0o755)


def install(cfg, start=True):
    if os.getuid() == 0:
        raise RuntimeError('Run this installer as your desktop user, without sudo.')
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise RuntimeError('This installer requires an x86-64 Linux host.')
    dependencies = ['virsh', 'qemu-img', 'genisoimage', 'systemctl', 'newuidmap', 'newgidmap', 'virt-manager', 'findmnt']
    missing = [item for item in dependencies if not shutil.which(item)]
    if missing:
        raise RuntimeError('Missing host tools: ' + ', '.join(missing) +
                           '. Install qemu-system-x86 qemu-utils libvirt-daemon-system virt-manager genisoimage uidmap.')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise RuntimeError('Your user needs access to /dev/kvm.')
    root = Path(cfg['root'])
    if (root / 'vm.json').exists():
        old = load(root)
        if old['shared'] != cfg['shared'] or old['domain'] != cfg['domain']:
            raise RuntimeError('Existing VM settings differ; existing files were retained.')
        if start:
            open_vm(old)
        return
    pending = root / 'installing.json'
    if root.exists() and any(root.iterdir()):
        if not pending.exists() or json.loads(pending.read_text()) != cfg:
            raise RuntimeError('VM userdata is not empty; refusing to overwrite it.')
    exists = subprocess.run(['virsh', '-c', 'qemu:///session', 'dominfo', cfg['domain']],
                            capture_output=True, env=host_environment())
    if exists.returncode == 0:
        raise RuntimeError('A VM with this name already exists; choose another name.')
    identity = getpass.getuser()
    subuid = subordinate_id('/etc/subuid', identity)
    subgid = subordinate_id('/etc/subgid', identity)
    root.mkdir(parents=True, mode=0o700); root.chmod(0o700)
    pending.write_text(json.dumps(cfg, indent=2)); pending.chmod(0o600)
    binary = ensure_daemon(root)
    base = root / 'ubuntu-base.qcow2'
    sums = root / 'image-SHA256SUMS'
    if not sums.exists():
        download(IMAGE_URL + 'SHA256SUMS', sums)
    checksum = next((line.split()[0] for line in sums.read_text().splitlines()
                     if line.split()[-1].lstrip('*') == IMAGE), None)
    if not checksum:
        raise RuntimeError('Ubuntu checksum manifest did not contain the selected cloud image.')
    if not base.exists():
        download(IMAGE_URL + IMAGE, base)
    digest = hashlib.sha256()
    with base.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != checksum:
        raise RuntimeError('Ubuntu image checksum mismatch; VM was not created.')
    base.chmod(0o600)
    if not (root / 'disk.qcow2').exists():
        execute(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', base,
                 root / 'disk.qcow2', str(cfg['disk_gib']) + 'G'])
    seed = root / 'seed'; seed.mkdir(mode=0o700, exist_ok=True)
    (seed / 'user-data').write_text('#cloud-config\n' + json.dumps(cloud_config(cfg), indent=2))
    (seed / 'meta-data').write_text(json.dumps({'instance-id': str(uuid.uuid4()), 'local-hostname': cfg['domain']}))
    (seed / 'network-config').write_text(json.dumps({'version': 2, 'ethernets': {
        'ethernet': {'match': {'name': 'en*'}, 'dhcp4': True}}}))
    execute(['genisoimage', '-quiet', '-output', root / 'seed.iso', '-volid', 'cidata',
             '-joliet', '-rock', seed / 'user-data', seed / 'meta-data', seed / 'network-config'])
    xml = root / 'domain.xml'; xml.write_text(domain_xml(cfg))
    text = service_text(cfg, binary, subuid, subgid)
    (root / 'virtiofs.service').write_text(text)
    register_service(cfg)
    execute(['virsh', '-c', 'qemu:///session', 'define', xml])
    (root / 'vm.json').write_text(json.dumps(cfg, indent=2)); (root / 'vm.json').chmod(0o600)
    pending.unlink()
    write_launcher(root)
    print('VM installed at ' + str(root), flush=True)
    print('Guest setup runs on first boot. Sign into NordVPN there before starting Steam.', flush=True)
    if start:
        open_vm(cfg)


def open_vm(cfg, desktop=True):
    if not Path(cfg['shared']).is_dir():
        raise RuntimeError('The configured shared games folder is unavailable.')
    if shared_mount(cfg['shared']) != cfg['shared_mount']:
        raise RuntimeError('The configured shared filesystem is not mounted; refusing to use a fallback folder.')
    register_service(cfg)
    try:
        execute(['systemctl', '--user', 'start', cfg['service'] + '.service'])
    except RuntimeError as error:
        journal = subprocess.run(['journalctl', '--user', '-u', cfg['service'] + '.service', '-n', '40', '--no-pager'], capture_output=True, text=True)
        raise RuntimeError(str(error) + '\n' + journal.stdout.strip()) from None
    for _ in range(40):
        socket_path = Path(cfg['socket'])
        if socket_path.exists() and stat.S_ISSOCK(socket_path.stat().st_mode):
            break
        time.sleep(.25)
    else:
        raise RuntimeError('The virtiofs daemon did not create its socket. Check its systemd user service.')
    state = execute(['virsh', '-c', 'qemu:///session', 'domstate', cfg['domain']],
                    capture_output=True, text=True).stdout.strip()
    if state == 'paused':
        execute(['virsh', '-c', 'qemu:///session', 'resume', cfg['domain']])
    elif state != 'running':
        execute(['virsh', '-c', 'qemu:///session', 'start', cfg['domain']])
    if not desktop:
        return
    subprocess.Popen(['virt-manager', '--connect', 'qemu:///session',
                      '--show-domain-console', cfg['domain']],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=host_environment())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'plan', 'open'), nargs='?', default='install')
    parser.add_argument('--root', type=Path, default=default_root(), help='VM userdata directory')
    parser.add_argument('--shared', type=Path, help='Merged games folder shared with the VM')
    parser.add_argument('--name', help='Optional unique libvirt domain name')
    parser.add_argument('--no-start', action='store_true', help='Create the VM without booting it')
    args = parser.parse_args()
    try:
        if args.command == 'open':
            open_vm(load(args.root))
        else:
            if args.shared is None:
                parser.error('--shared is required when creating a VM')
            cfg = configuration(args.root, args.shared, args.name)
            if args.command == 'plan':
                print(json.dumps(cfg, indent=2))
            else:
                install(cfg, start=not args.no_start)
    except Exception as error:
        parser.exit(1, 'VM setup failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
