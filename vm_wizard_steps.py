"""Four-stage setup: configure, provision, install, finish authentication."""
from pathlib import Path
import re
from PyQt6.QtCore import QSettings,QTimer
from PyQt6.QtWidgets import QLabel,QLineEdit,QPushButton
from .desktop_links import open_account_link
from .settings import Preferences

STEPS=('Configure downloads and VPN','Create VM and connect NordVPN','Install Steam and LuaTools','Steam sign-in','LuaTools and optional Hubcap')


def configure(dialog):
    from .vm_setup import prepare_vm,install_nordvpn,provision_vm
    from .vm_backend import VMSteamRuntime
    from .credentials import VMAuthenticationWallet
    settings=QSettings('Playlite','SteamDownloader')
    exists=(dialog.network.profile/'vm.json').exists()
    old=int(settings.value('vm_setup_step',0))
    migrated=3 if old>=6 else 2 if old==5 else 1 if old>=1 else 0
    dialog.wizard_step=max(0,min(int(settings.value('vm_setup_phase',migrated)),4)) if exists else 0
    try:saved=VMAuthenticationWallet().read() or {}
    except (RuntimeError,ValueError):saved={}
    token=saved.get('nord_token','')
    layout=dialog.layout()
    hint=QLabel();hint.setWordWrap(True);layout.insertWidget(layout.indexOf(dialog.status),hint)
    secret=QLineEdit();secret.setEchoMode(QLineEdit.EchoMode.Password);secret.setMaxLength(256);secret.setText(token)
    layout.insertWidget(layout.indexOf(dialog.status),secret)
    link=QPushButton();layout.insertWidget(layout.indexOf(dialog.status),link)
    steam=QLabel('Steam: use Sign into Steam below. Its own sign-in screen includes Steam Guard and phone approval when required.')
    steam.setWordWrap(True);layout.insertWidget(layout.indexOf(dialog.status),steam)
    moon_label=QLabel('LuaTools / Moon · fresh Discord /login code')
    moon=QLineEdit();moon.setEchoMode(QLineEdit.EchoMode.Password);moon.setMaxLength(2048);moon.setPlaceholderText('Enter a fresh LuaTools Discord code')
    hub_label=QLabel('Hubcap Manifest API key · optional')
    hub=QLineEdit();hub.setEchoMode(QLineEdit.EchoMode.Password);hub.setMaxLength(2048);hub.setPlaceholderText('Leave empty to skip Hubcap')
    for widget in (moon_label,moon,hub_label,hub):layout.insertWidget(layout.indexOf(dialog.status),widget)
    dialog.wizard_secret=secret;dialog.wizard_moon=moon;dialog.wizard_hubcap=hub
    dialog.wizard_controls=(secret,link,moon,hub)
    for control in dialog.token_controls:control.hide()
    dialog.auth_settings_button.hide();dialog.buttons[2].hide()
    dialog.login_timer.stop();dialog.login_timer.timeout.disconnect()
    dialog.buttons[0].clicked.disconnect();dialog.buttons[1].clicked.disconnect()
    def steam_sign_in():
        from .steam_sign_in_dialog import SteamSignInDialog
        if SteamSignInDialog(dialog.network,dialog).exec():
            phase(4)
    dialog.buttons[1].clicked.connect(steam_sign_in)
    def preferences():return Preferences(settings.value('country',''),settings.value('protocol','tcp')).validate()
    def render():
        n=dialog.wizard_step
        dialog.stage.setText('Setup complete' if dialog.ready else f'Step {n+1} of 5 · {STEPS[n]}')
        hint.setText(('Choose the shared download folder and enter your NordVPN access token. Installation will run automatically.',
                      'Creating the VM, installing NordVPN, signing in and verifying the connection. No further input is needed here.',
                      'Installing and updating Steam, LuaTools and LuaMoon over the VPN. Account sign-in comes next.',
                      'Sign into Steam using its live screen. Steam Guard appears when needed.',
                      'Enter a fresh LuaTools Discord code and an optional Hubcap key, then verify.')[n] if not dialog.ready else 'Steam Downloader is ready. Your games use the shared folder directly.')
        secret.setVisible(n==0);secret.setPlaceholderText('NordVPN access token')
        dialog.shared_folder.setVisible(n==0);dialog.folder_button.setVisible(n==0)
        link.setVisible(n in (0,4) and not dialog.ready);link.setText('Open Nord Account' if n==0 else 'Open LuaTools Discord')
        steam.setVisible(n==3 and not dialog.ready)
        for widget in (moon_label,moon,hub_label,hub):widget.setVisible(n==4 and not dialog.ready)
        dialog.buttons[1].setVisible(False)
        dialog.buttons[1].setText('Sign into Steam…')
        dialog.buttons[0].setText('Open Downloader' if dialog.ready else ('Start installation','Continue VM installation','Continue Steam installation','Sign into Steam…','Verify and finish')[n])
        dialog.status.setText('Setup complete.' if dialog.ready else 'Progress is saved. Installation resumes from completed work.')
        if hasattr(dialog,'update_step_visuals'):dialog.update_step_visuals(5 if dialog.ready else n)
        if hasattr(dialog,'update_feedback_visuals'):dialog.update_feedback_visuals(False)
    def phase(value):
        dialog.wizard_step=value;settings.setValue('vm_setup_phase',value);render()
        dialog.append_log('Current setup step: '+STEPS[value])
    def action():
        nonlocal token
        if dialog.jobs:return
        if dialog.ready:dialog.accept();return
        n=dialog.wizard_step
        if n==0:
            path=Path(dialog.shared_folder.text()).expanduser();candidate=secret.text().strip()
            if not path.is_absolute():dialog.status.setText('Choose an absolute download folder.');return
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',candidate):dialog.status.setText('Enter a valid NordVPN access token.');return
            try:VMAuthenticationWallet().save({**saved,'nord_token':candidate})
            except (RuntimeError,ValueError) as error:dialog.status.setText(str(error));return
            token=candidate;secret.clear();settings.setValue('download_root',str(path));phase(1)
            QTimer.singleShot(0,action);return
        if n==1:
            def provision(progress):
                prepare_vm(dialog.network,progress);install_nordvpn(dialog.network,progress)
                code,out,_=dialog.network.agent.execute(['/usr/sbin/runuser','-u','ubuntu','--','/usr/bin/nordvpn','account'],timeout=15)
                if not (code==0 and b'email' in out.lower()):
                    if not token:raise RuntimeError('NordVPN needs an access token. Open Authentication settings, save a token, and reopen setup.')
                    progress('Signing into NordVPN…');dialog.network.agent.rpc({'command':'nord_login_token','token':token})
                return dialog.network.connect(preferences(),progress=progress)
            def continue_install():phase(2);QTimer.singleShot(0,action)
            dialog.task(provision,continue_install);return
        if n==2:
            dialog.task(lambda progress:provision_vm(dialog.network,preferences(),progress),lambda:phase(3));return
        if n==3:
            steam_sign_in();return
        code=moon.text().strip();key=hub.text().strip();moon.clear();hub.clear()
        def verify(progress):
            dialog.network.check()
            runtime=VMSteamRuntime(dialog.network)
            state=runtime.request('status')
            if not state.get('steam_running') or not state.get('moon_installed'):
                raise RuntimeError('Steam and LuaTools must be running before verification. Continue Steam installation or open the VM desktop.')
            if not state.get('steam_session_saved'):raise RuntimeError('Complete Steam sign-in and Steam Guard approval in the VM, then verify again.')
            status=runtime.request('authentication_status')
            if status.get('luatools',{}).get('state')!='saved_session':
                if not code:raise RuntimeError('Enter a fresh LuaTools Discord /login code, then verify again.')
                progress('Signing into LuaTools…')
                status=runtime.request('provider_login',provider='luatools',credential=code)
                if status.get('luatools',{}).get('state')!='saved_session':raise RuntimeError('LuaTools sign-in was not verified. Request a fresh Discord code and retry.')
            if key:
                progress('Verifying optional Hubcap key…')
                status=runtime.request('provider_login',provider='hubcap',credential=key)
                if status.get('hubcap',{}).get('state')!='verified':raise RuntimeError('Hubcap key was not verified. Retry with a valid key, or leave it empty to skip.')
            settings.setValue('vm_ready_profile',str(dialog.network.profile))
            return 'Steam and LuaTools are ready. Hubcap is optional.'
        def complete():dialog.ready=True;render()
        dialog.task(verify,complete)
    link.clicked.connect(lambda:open_account_link('https://my.nordaccount.com/' if dialog.wizard_step==0 else 'https://discord.gg/luatools'))
    dialog.buttons[0].clicked.connect(action)
    dialog.render_wizard_step=render;dialog.install=action
    from .setup_visuals import build
    build(dialog,STEPS);render()
    dialog.append_log('Current setup step: '+STEPS[dialog.wizard_step])
