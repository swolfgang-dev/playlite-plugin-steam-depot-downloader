"""Idempotent provisioning for the private Steam desktop."""
import hashlib
import platform
from pathlib import Path
import shutil
import subprocess
import time
from .steam_runtime import RUNTIME_IMAGE
from .constants import OWNER_LABEL, OWNER

LABEL='io.playlite.steam.setup'


def image_revision(context):
    digest=hashlib.sha256()
    for path in sorted(context.iterdir()):
        if path.is_file():
            digest.update(path.name.encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_image(progress):
    if platform.system()!='Linux' or platform.machine().lower() not in ('x86_64','amd64'):
        raise RuntimeError('Isolated Steam requires an x86-64 Linux host.')
    for executable in ('docker','socat'):
        if not shutil.which(executable):raise RuntimeError(f'Install {executable} before setting up isolated Steam.')
    limit=Path('/proc/sys/user/max_user_namespaces')
    if limit.exists() and int(limit.read_text())==0:
        raise RuntimeError('Enable Linux user namespaces before setting up Steam.')
    result=subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('Docker is unavailable. Start its daemon and give your user access to it.')
    context=Path(__file__).parent/'tools/steam'
    revision=image_revision(context)
    result=subprocess.run(['docker','image','inspect','--format','{{index .Config.Labels "'+LABEL+'"}}',RUNTIME_IMAGE],capture_output=True,text=True,timeout=30)
    if result.returncode==0 and result.stdout.strip()==revision:
        progress('Container image is ready.');return
    progress('Building the isolated Steam image. The first installation can take several minutes…')
    # Stream bounded build output directly to the setup log; credentials are never passed to the build.
    with subprocess.Popen(['docker','build','--label',LABEL+'='+revision,'--label',OWNER_LABEL+'='+OWNER,'-t',RUNTIME_IMAGE,str(context)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True) as process:
        for line in process.stdout:progress(line.rstrip()[:2000])
        if process.wait():raise RuntimeError('Container image build failed. See the setup log and retry.')


def wait_for(runtime,predicate,timeout,message):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            state=runtime.request('status')
        except (OSError,RuntimeError):state=None
        if state is not None and predicate(state):return state
        time.sleep(1)
    raise RuntimeError(message)


def provision(runtime,preferences,credentials,progress):
    if getattr(runtime.network, 'is_vm', False) is True:
        from .vm_setup import provision_vm
        return provision_vm(runtime.network,preferences,progress)
    ensure_image(progress)
    progress('Connecting the isolated VPN…')
    if runtime.network.container:runtime.network.check()
    else:runtime.network.connect(preferences,*credentials,progress=progress)
    progress('Starting the private Steam desktop…');runtime.start()
    state=wait_for(runtime,lambda state:True,90,'The Steam desktop did not start. Retry setup.')
    progress('Updating Steam. This may take several minutes…');runtime.request('start')
    if not state['moon_installed']:
        wait_for(runtime,lambda state:state.get('steam_ready'),900,'Steam update did not finish. Open the desktop to inspect it, then retry setup.')
        progress('Installing LuaMoon…');runtime.request('install_moon')
        state=wait_for(runtime,lambda state:not state['moon_installing'],900,'LuaMoon installation timed out. Open the desktop and retry setup.')
        if state.get('moon_install_exit')!=0 or not state['moon_installed']:
            raise RuntimeError('LuaMoon installation failed. Its log is in the private runtime control folder (moon-setup.log). Retry setup.')
    progress('Starting Steam with LuaMoon…');runtime.request('start')
    return 'Setup complete. Sign into Steam and LuaMoon in the isolated desktop if prompted.'
