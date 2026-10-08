"""Provision and open the isolated Steam desktop."""
import socket
import subprocess
import os
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QThreadPool,QTimer,QUrl,QSettings
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,QLineEdit,QFileDialog,QProgressBar
from .steam_runtime import SteamRuntime
from .desktop_links import open_account_link

class SteamRuntimeDialog(QDialog):
    def __init__(self,network,parent=None):
        super().__init__(parent)
        from .vm_backend import enabled,VMNetwork
        if getattr(network,'is_vm',False) is not True and enabled():network=VMNetwork()
        self.vm=getattr(network,'is_vm',False) is True
        self.runtime=SteamRuntime(network) if not self.vm or enabled() else None
        self.network=network
        self.jobs=set();self.relay=None
        self.waiting_login=False;self.setup_finished=False
        self.ready=False
        self.setWindowTitle('Isolated Steam — Playlite');self.resize(680,400)
        layout=QVBoxLayout(self)
        intro=QLabel('Setup creates or upgrades the Steam VM, installs LuaTools and LuaMoon, and keeps games on the shared drive. Sign into NordVPN, Steam, and providers inside the VM desktop.' if self.vm else 'Setup installs the private Steam desktop and LuaMoon using your saved NordVPN credentials. Sign into Steam and your providers inside that desktop. Existing sessions and game files are retained.')
        intro.setWordWrap(True);layout.addWidget(intro)
        if self.vm:
            self.setWindowTitle('Steam Downloader setup — Playlite')
            intro.setText('Steam downloads into your shared game folder. Playlite uses the same files, with no copy step. Setup resumes where you left off.')
            authentication=QPushButton('Authentication settings…')
            def edit_authentication():
                from .vm_authentication_dialog import VMAuthenticationDialog
                VMAuthenticationDialog(self.network,self).exec()
            authentication.clicked.connect(edit_authentication);layout.addWidget(authentication)
            self.auth_settings_button=authentication
            self.stage=QLabel('Step 1 of 4 · Game folder');layout.addWidget(self.stage)
            from .download_location import default_download_root
            folder_line=QHBoxLayout();self.shared_folder=QLineEdit(default_download_root())
            self.shared_folder.setReadOnly(enabled())
            browse=QPushButton('Choose game folder…');browse.setEnabled(not enabled())
            self.folder_button=browse
            def choose_folder():
                folder=QFileDialog.getExistingDirectory(self,'Shared Steam download folder',self.shared_folder.text())
                if folder:self.shared_folder.setText(folder)
            browse.clicked.connect(choose_folder)
            folder_line.addWidget(self.shared_folder);folder_line.addWidget(browse);layout.addLayout(folder_line)
        authenticate=QPushButton('Sign into NordVPN inside VM…' if self.vm else 'Authenticate NordVPN…')
        def credentials():
            if self.vm:
                self.network.open_desktop()
                self.task(lambda progress:self.network.agent.rpc({'command':'open_nord'})['message'])
                return
            from .authentication_dialog import CredentialDialog
            CredentialDialog('NordVPN',self.network,self).exec()
        authenticate.clicked.connect(credentials);layout.addWidget(authenticate)
        self.authenticate_button=authenticate
        if self.vm:authenticate.hide()
        if self.vm:
            self.nord_token_panel=QLabel('Sign into Nord Account in your browser, open NordVPN → Advanced settings → Get access token, then paste the token here. It is sent to the VM and is not saved by Playlite.')
            self.nord_token_panel.setWordWrap(True);layout.addWidget(self.nord_token_panel)
            self.nord_token=QLineEdit();self.nord_token.setEchoMode(QLineEdit.EchoMode.Password)
            self.nord_token.setPlaceholderText('Nord Account access token')
            token_line=QHBoxLayout();token_line.addWidget(self.nord_token)
            account=QPushButton('Open Nord Account');account.clicked.connect(lambda:open_account_link('https://my.nordaccount.com/'))
            token_line.addWidget(account)
            token_login=QPushButton('Sign in and continue');token_login.clicked.connect(self.login_token)
            token_line.addWidget(token_login);layout.addLayout(token_line)
            self.token_controls=(self.nord_token_panel,self.nord_token,account,token_login)
            for control in self.token_controls:control.hide()
        self.status=QLabel('Ready to set up isolated Steam.');self.status.setWordWrap(True);layout.addWidget(self.status)
        if self.vm:
            self.activity=QProgressBar();self.activity.setRange(0,1);self.activity.setValue(0);self.activity.setTextVisible(False)
            self.activity.hide()
            layout.addWidget(self.activity)
            self.status.setText('Choose the folder where Steam should download your games, then continue.')
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.document().setMaximumBlockCount(500);layout.addWidget(self.log)
        state=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))
        self.log_path=state/'playlite/steam-vm-setup.log'
        log_controls=QHBoxLayout()
        copy_log=QPushButton('Copy log');copy_log.clicked.connect(lambda:QApplication.clipboard().setText(self.log.toPlainText()))
        open_log=QPushButton('Open log');open_log.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path))))
        log_controls.addWidget(copy_log);log_controls.addWidget(open_log);layout.addLayout(log_controls)
        if self.vm:
            self.details_toggle=QPushButton('Show setup log');self.details_toggle.setCheckable(True)
            layout.insertWidget(layout.indexOf(self.log),self.details_toggle)
            self.log.hide();copy_log.hide();open_log.hide()
            def show_details(visible):
                self.log.setVisible(visible);copy_log.setVisible(visible);open_log.setVisible(visible)
                self.details_toggle.setText('Hide setup log' if visible else 'Show setup log')
            self.details_toggle.toggled.connect(show_details)
        self.append_log('Setup window opened.')
        controls=QHBoxLayout();controls.setSpacing(10);self.buttons=[]
        for text,callback in [('Set up isolated Steam',self.install),('Open desktop',self.desktop),('Refresh status',self.refresh)]:
            button=QPushButton(text);button.clicked.connect(callback);controls.addWidget(button);self.buttons.append(button)
        layout.addLayout(controls)
        self.close_button=QPushButton('Close');self.close_button.clicked.connect(self.close);layout.addWidget(self.close_button)
        if self.vm:
            self.buttons[0].setText('Continue setup')
            self.buttons[2].setText('Check sign-in')
            self.buttons[2].hide()
            if not enabled():self.buttons[0].setText('Create VM and continue')
            self.buttons[1].setText('Open VM for sign-in…')
            self.buttons[1].setEnabled(enabled())
            self.buttons[1].setVisible(enabled())
            self.buttons[0].setDefault(True)
            self.login_timer=QTimer(self);self.login_timer.setInterval(8000)
            self.login_timer.timeout.connect(self.poll_login)
            from .vm_wizard_steps import configure
            configure(self)

    def poll_login(self):
        if self.jobs:return
        if self.setup_finished and not self.ready:
            self.refresh();return
        if not self.waiting_login:return
        def check(progress):
            code,out,_=self.network.agent.execute(['/usr/sbin/runuser','-u','ubuntu','--','/usr/bin/nordvpn','account'],timeout=15)
            return 'Signed in' if code==0 and b'email' in out.lower() else 'Waiting for NordVPN sign-in in the VM…'
        def checked():
            if self.status.text()=='Signed in':
                self.waiting_login=False;self.login_timer.stop();self.install()
        self.task(check,checked)

    def login_token(self):
        if self.jobs:return
        token=self.nord_token.text().strip();self.nord_token.clear()
        if not token:
            self.status.setText('Paste your Nord Account access token.');return
        def sign_in(progress):
            self.network.agent.rpc({'command':'nord_login_token','token':token})
            self.waiting_login=False;return 'NordVPN signed in.'
        self.task(sign_in,self.install)

    def append_log(self,message):
        line=f'[{datetime.now().astimezone().isoformat(timespec="seconds")}] {message}'
        self.log.appendPlainText(line)
        if self.vm and not hasattr(self,'wizard_step'):
            if 'Creating Steam VM' in message or 'Waiting for VM desktop' in message:self.stage.setText('Step 2 of 4 · Creating VM')
            elif 'Updating Steam' in message or 'Installing LuaTools' in message or 'command bridge' in message:self.stage.setText('Step 3 of 4 · Installing Steam and LuaTools')
        try:
            self.log_path.parent.mkdir(parents=True,exist_ok=True)
            with self.log_path.open('a',encoding='utf-8') as output:output.write(line+'\n')
            self.log_path.chmod(0o600)
        except OSError as error:
            self.log.appendPlainText(f'Could not save setup log: {error}')

    def task(self,operation,after=None):
        if self.jobs:return
        from .plugin import Job
        result=[]
        def run():
            message=operation(job.signals.progress.emit)
            if isinstance(message,dict):
                message=f'Steam: {"running" if message["steam_running"] else "stopped"} · LuaMoon: {"installing" if message["moon_installing"] else "installed" if message["moon_installed"] else "not installed"}'
            result.append(True);return str(message)
        job=Job(run);self.jobs.add(job);self.status.setText('Working…')
        if hasattr(self,'update_feedback_visuals'):self.update_feedback_visuals(False)
        if self.vm:self.activity.setRange(0,0);self.activity.show()
        for button in [*self.buttons,self.close_button]:button.setEnabled(False)
        if self.vm:self.auth_settings_button.setEnabled(False)
        for control in getattr(self,'wizard_controls',()):control.setEnabled(False)
        self.authenticate_button.setEnabled(False)
        if self.vm:
            for control in self.token_controls:control.setEnabled(False)
        job.signals.progress.connect(self.append_log)
        if hasattr(self,'wizard_step'):job.signals.progress.connect(self.status.setText)
        def finished(message):
            self.jobs.discard(job);self.status.setText(message);self.append_log(message)
            if self.vm:self.activity.setRange(0,1);self.activity.hide()
            for button in [*self.buttons,self.close_button]:button.setEnabled(True)
            if self.vm:self.auth_settings_button.setEnabled(True)
            for control in getattr(self,'wizard_controls',()):control.setEnabled(True)
            self.authenticate_button.setEnabled(True)
            if self.vm:
                for control in self.token_controls:
                    control.setEnabled(True);control.setVisible(self.waiting_login)
            if self.vm and (self.network.profile/'vm.json').exists():
                self.shared_folder.setReadOnly(True);self.folder_button.setEnabled(False)
                self.buttons[1].show()
            if self.vm and self.waiting_login:self.login_timer.start()
            if self.vm and self.waiting_login:self.stage.setText('Step 2 of 4 · Sign into NordVPN')
            elif self.vm and not result:
                self.details_toggle.setChecked(True)
                if self.setup_finished:
                    self.stage.setText('Step 4 of 4 · Steam and provider sign-in')
                    self.buttons[2].show()
                    self.login_timer.start()
            if hasattr(self,'update_feedback_visuals'):self.update_feedback_visuals(not bool(result))
            if result and after:after()
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)

    def install(self):
        if self.jobs:return
        if self.vm and self.ready:
            self.accept();return
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.network):
            self.status.setText('Wait for queued downloads to finish before running setup.');return
        if self.vm:
            from .vm_setup import provision_vm,VMLoginRequired
            from .settings import Preferences
            settings=QSettings('Playlite','SteamDownloader')
            preferences=Preferences(settings.value('country',''),settings.value('protocol','tcp')).validate()
            from .credentials import VMAuthenticationWallet
            try:authentication=VMAuthenticationWallet().read() or {}
            except (RuntimeError,ValueError):authentication={}
            def setup(progress):
                if not (self.network.profile/'vm.json').exists():
                    shared=Path(self.shared_folder.text()).expanduser()
                    if not shared.is_absolute():raise RuntimeError('Choose an absolute shared game folder.')
                    settings.setValue('download_root',str(shared))
                self.waiting_login=False
                try:message=provision_vm(self.network,preferences,progress,authentication)
                except VMLoginRequired:
                    self.waiting_login=True
                    raise
                self.runtime=SteamRuntime(self.network)
                self.setup_finished=True
                return message
            self.task(setup,self.refresh)
            return
        from .credentials import Wallet
        from .settings import Preferences
        from .steam_setup import provision
        settings=QSettings('Playlite','SteamDownloader')
        try:
            preferences=Preferences(settings.value('country',''),settings.value('protocol','udp')).validate()
            credentials=Wallet().read()
        except Exception:
            self.status.setText('Authenticate NordVPN in settings first, then retry setup.');return
        self.task(lambda progress:provision(self.runtime,preferences,credentials,progress),self.open_desktop)

    def refresh(self):
        if self.vm:
            if self.jobs:return
            def verify(progress):
                self.network.cancelled.clear()
                self.network.boot(progress)
                self.network.check()
                self.runtime=SteamRuntime(self.network)
                state=self.runtime.request('status')
                if not state.get('steam_ready') or not state.get('moon_installed'):
                    raise RuntimeError('Continue setup to finish installing Steam and LuaTools.')
                self.setup_finished=True
                self.runtime.request('start')
                if not state.get('steam_session_saved'):
                    raise RuntimeError('Steam is installed. Open Steam sign-in and approve Steam Guard if requested. Setup will check automatically.')
                auth=self.runtime.request('authentication_status')
                if auth.get('luatools',{}).get('state')!='saved_session' and auth.get('hubcap',{}).get('state')!='verified':
                    raise RuntimeError('Sign into LuaTools or configure Hubcap in the VM, then verify again.')
                QSettings('Playlite','SteamDownloader').setValue('vm_ready_profile',str(self.network.profile))
                return 'Ready to download. Steam may prompt for its account sign-in in the VM.'
            self.task(verify,self.show_ready)
            return
        if self.vm and self.runtime is None:
            self.status.setText('Steam VM is not installed. Run setup first.');return
        self.task(lambda progress:self.runtime.request('status'))

    def show_ready(self):
        self.ready=True
        self.login_timer.stop()
        self.stage.setText('Setup complete')
        self.status.setText('Ready to download. Games will be installed in '+self.shared_folder.text()+'.')
        self.buttons[0].setText('Open Downloader')
        self.buttons[2].hide()

    def desktop(self):
        if self.vm:
            self.task(lambda progress:(self.network.cancelled.clear(),self.network.boot(progress,desktop=True),'Steam VM desktop opened.')[-1])
            return
        def start(progress):
            from .steam_setup import wait_for
            self.runtime.start()
            wait_for(self.runtime,lambda state:True,90,'The isolated desktop did not start. Run setup first.')
            self.runtime.request('start')
            return 'Steam and provider authentication are available in the isolated desktop.'
        self.task(start,self.open_desktop)

    def open_desktop(self):
        if self.vm:
            self.network.open_desktop();return
        shared=vars(self.network).get('_steam_desktop_relay')
        if shared and shared[0].poll() is None:
            self.relay,self.port=shared
        if self.relay is None or self.relay.poll() is not None:
            with socket.socket() as connection:
                connection.bind(('127.0.0.1',0));self.port=connection.getsockname()[1]
            try:
                self.relay=subprocess.Popen(['socat',f'TCP-LISTEN:{self.port},bind=127.0.0.1,reuseaddr,fork','UNIX-CONNECT:web.sock'],cwd=self.runtime.root/'control',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except OSError:
                self.status.setText('Install socat to open the isolated desktop.');return
        self.network._steam_desktop_relay=(self.relay,self.port)
        QTimer.singleShot(500,lambda:QDesktopServices.openUrl(QUrl(f'http://127.0.0.1:{self.port}/vnc.html?autoconnect=1&resize=scale')))

    def closeEvent(self,event):
        if self.jobs:event.ignore();return
        if self.vm:self.login_timer.stop()
        from .vpn_lifecycle import disconnect_when_idle
        # Settings and downloader own their connection until their own close
        # handlers release it. A child setup popup must not release that VPN.
        owner=self.parentWidget()
        while owner is not None:
            if (hasattr(owner,'settings_closed') and not owner.settings_closed) or getattr(owner,'network',None) is self.network:
                break
            owner=owner.parentWidget()
        if owner is None:disconnect_when_idle(self.network)
        super().closeEvent(event)
