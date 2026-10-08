"""Install or upgrade the VM used by the full Steam downloader."""
from pathlib import Path
import time
from .vm_backend import installer_module, update_runtime, VMSteamRuntime
from .download_location import default_download_root

class VMLoginRequired(RuntimeError):
    """The installed VM is waiting for the user's NordVPN sign-in."""


def prepare_vm(network, progress):
    from .vpn_lifecycle import has_downloads
    if has_downloads(network):
        raise RuntimeError('Pause or cancel queued downloads before VM setup.')
    network.cancelled.clear()
    installer = installer_module()
    if not (network.profile / 'vm.json').exists():
        shared = Path(default_download_root()).expanduser()
        shared.mkdir(parents=True, exist_ok=True)
        progress('Creating Steam VM. Initial installation may take several minutes…')
        installer.install(installer.configuration(network.profile, shared), start=False)
    network.boot(progress, desktop=False)
    # Wait for first-boot package setup before modifying the installed bridge.
    deadline = time.monotonic() + 1200
    while True:
        network.check_cancelled()
        code, _, _ = network.agent.execute(['/usr/bin/test', '-f', '/var/lib/playlite-vm/setup-complete'], timeout=15)
        if code == 0:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('VM package setup did not finish. Check /var/log/playlite-vm-setup.log in its desktop.')
        progress('Waiting for VM desktop installation…')
        time.sleep(5)
    update_runtime(network, progress)
    return 'VM installed. Continue to install NordVPN.'


def install_nordvpn(network, progress):
    network.boot(progress)
    code, _, _ = network.agent.execute(['/usr/bin/test','-f','/var/lib/playlite-vm/nordvpn-installed'],timeout=15)
    if code != 0:
        progress('Installing NordVPN inside the VM…')
        script = Path(__file__).parent/'tools/vm/install-nordvpn.sh'
        network.agent.write_file('/var/tmp/playlite-install-nordvpn-step.sh',script.read_bytes())
        code, _, _ = network.agent.execute(['/usr/bin/bash','/var/tmp/playlite-install-nordvpn-step.sh'],timeout=900)
        if code != 0:raise RuntimeError('NordVPN installation failed. Retry this step; guest log: /var/log/playlite-nordvpn-install.log')
    return 'NordVPN installed. Enter your access token.'


def _provision_vm(network, preferences, progress, authentication=None):
    authentication = authentication or {}
    prepare_vm(network, progress)
    install_nordvpn(network, progress)
    account_code, account, _ = network.agent.execute(["/usr/sbin/runuser","-u","ubuntu","--","/usr/bin/nordvpn","account"],timeout=15)
    if authentication.get("nord_token") and not (account_code == 0 and b"email" in account.lower()):
        progress("Signing into NordVPN with saved authentication…")
        network.agent.rpc({"command":"nord_login_token", "token":authentication["nord_token"]})
    try:
        network.connect(preferences, progress=progress)
    except RuntimeError as error:
        code,out,_=network.agent.execute(['/usr/sbin/runuser','-u','ubuntu','--','/usr/bin/nordvpn','account'],timeout=15)
        if code==0 and b'email' in out.lower():
            raise RuntimeError('NordVPN is signed in, but the VPN connection failed: '+str(error)) from None
        raise VMLoginRequired('Sign into NordVPN using a Nord Account token below, or use Open desktop. Setup continues after sign-in.') from None
    progress('Installing Steam inside the VM…')
    code, _, _ = network.agent.execute(['/usr/bin/env','DEBIAN_FRONTEND=noninteractive','/usr/bin/apt-get','install','-y','steam-installer','xdotool','imagemagick'],timeout=900)
    if code != 0:raise RuntimeError('Steam package installation failed. Retry this step.')
    progress('Updating Steam and installing LuaTools inside the VM…')
    network.agent.rpc({'command':'setup'})
    runtime=VMSteamRuntime(network)
    deadline=time.monotonic()+1900
    phase=None
    while True:
        network.check_cancelled()
        state=runtime.request('status')
        if state.get('setup_phase') and state['setup_phase']!=phase:
            phase=state['setup_phase'];progress(phase)
        if not state.get('setup_running'):
            if state.get('setup_error'):raise RuntimeError(state['setup_error'])
            if not state.get('steam_ready') or not state.get('moon_installed'):
                raise RuntimeError('Steam VM setup is incomplete. Check its desktop.')
            break
        if time.monotonic()>=deadline:raise RuntimeError('VM Steam setup timed out. Check its desktop before retrying.')
        time.sleep(2)
    runtime.request('start')
    for provider, field in (('luatools','luatools_code'), ('hubcap','hubcap_key')):
        if authentication.get(field):
            status=runtime.request('authentication_status')
            expected='saved_session' if provider=='luatools' else 'verified'
            if status.get(provider,{}).get('state')!=expected:
                progress('Applying '+provider+' authentication…')
                runtime.request('provider_login',provider=provider,credential=authentication[field])
    return 'VM setup complete. Sign into Steam and LuaTools/providers in its desktop if prompted.'


def provision_vm(network, preferences, progress, authentication=None):
    """Retry transient transport failures without discarding completed setup."""
    for attempt in range(3):
        try:
            return _provision_vm(network, preferences, progress, authentication)
        except VMLoginRequired:
            raise
        except RuntimeError as error:
            transient = any(word in str(error).lower() for word in
                            ('timed out', 'unavailable', 'connection failed', 'bridge is not ready'))
            if attempt == 2 or not transient:
                raise
            progress('Temporary connection problem. Retrying completed setup in 5 seconds…')
            network.check_cancelled()
            time.sleep(5)
