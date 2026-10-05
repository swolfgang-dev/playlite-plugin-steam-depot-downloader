"""Explicit manifest and Steam login workflow; never uses a host Steam profile."""
import tempfile
import base64
import uuid
from pathlib import Path
from PyQt6.QtCore import QProcess, QThreadPool, QSettings
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QPushButton,
                            QLabel, QPlainTextEdit, QFileDialog, QHBoxLayout, QWidget, QVBoxLayout)
from .moon import Moon
from .credentials import MoonSessionWallet, SteamSessionWallet, HubcapKeyWallet
from .providers import SOURCES, Transport, prepare_depot
from .worker import Request, worker_args, require_download_success

class DownloadDialog(QDialog):
    def __init__(self, network, parent=None, authentication=False):
        super().__init__(parent)
        self.authentication = authentication
        self.network = network
        self.transport = Transport(network)
        self.moon = Moon(self.transport, MoonSessionWallet())
        self.rows = []
        self.jobs = set()
        self.process = None
        self.temporary = None
        self.container = None
        self.output = ''
        self.setWindowTitle('Steam Depot Downloader — Playlite')
        self.resize(850, 720)
        form = QFormLayout(self)
        auth_rows = []; download_rows = []
        notice = QLabel('Downloads use a separate Steam session inside the VPN. Use an account that owns the game. Moon login is saved in KWallet and restored automatically. Download one selected depot at a time; the destination must be empty.')
        notice.setWordWrap(True)
        form.addRow(notice)
        self.appid = QLineEdit('736260')
        form.addRow('Steam App ID', self.appid); download_rows.append(self.appid)
        self.provider = QComboBox(); self.provider.addItems(SOURCES)
        form.addRow('Manifest provider', self.provider); download_rows.append(self.provider)
        self.code = QLineEdit(); self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setPlaceholderText('Run /login in the LuaTools Discord; paste the six-character code')
        login = QPushButton('Authenticate'); login.clicked.connect(self.login)
        line = QHBoxLayout(); line.addWidget(self.code); line.addWidget(login)
        logout = QPushButton('Sign out'); logout.clicked.connect(lambda: self.task(self.moon.logout, self.status.setText))
        line.addWidget(logout)
        form.addRow('Moon login code', line); auth_rows.append(line)
        discord = QLabel('<a href="https://discord.gg/luatools">Open LuaTools Discord</a> — run /login to get a six-character code.')
        discord.setOpenExternalLinks(True); discord.setWordWrap(True)
        form.addRow(discord); auth_rows.append(discord)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        save_key = QPushButton('Save API key'); save_key.clicked.connect(self.save_key)
        line = QHBoxLayout(); line.addWidget(self.key); line.addWidget(save_key)
        form.addRow('Hubcap API key', line); auth_rows.append(line)
        self.fetch = QPushButton('Fetch manifest pack'); self.fetch.clicked.connect(self.fetch_pack)
        form.addRow(self.fetch); download_rows.append(self.fetch)
        self.depot = QComboBox(); form.addRow('Depot / manifest', self.depot); download_rows.append(self.depot)
        self.username = QLineEdit(QSettings("Playlite", "SteamDownloader").value("steam_account", ""))
        forget = QPushButton('Forget Steam login')
        forget.clicked.connect(self.forget_steam)
        line = QHBoxLayout(); line.addWidget(self.username); line.addWidget(forget)
        form.addRow('Steam account name', line); auth_rows.append(line)
        self.destination = QLineEdit()
        browse = QPushButton('Browse…'); browse.clicked.connect(self.browse)
        line = QHBoxLayout(); line.addWidget(self.destination); line.addWidget(browse)
        form.addRow('Download folder', line); download_rows.append(line)
        self.start_button = QPushButton('Download selected depot'); self.start_button.clicked.connect(self.download)
        form.addRow(self.start_button)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); form.addRow(self.log)
        self.response = QLineEdit(); self.response.setEchoMode(QLineEdit.EchoMode.Password)
        self.response.setPlaceholderText('Enter Steam password or Steam Guard code when requested in the log')
        self.send = QPushButton('Send securely'); self.send.clicked.connect(self.send_response)
        self.response.returnPressed.connect(self.send_response)
        line = QHBoxLayout(); line.addWidget(self.response); line.addWidget(self.send)
        form.addRow('Steam login response', line); auth_rows.append(line)
        self.status = QLabel('Connect the VPN and build the native worker image before continuing.')
        self.status.setWordWrap(True); form.addRow(self.status)
        self.cancel = QPushButton('Close'); self.cancel.clicked.connect(self.close_or_cancel)
        form.addRow(self.cancel)
        self.busy = False
        for row in (download_rows if authentication else auth_rows):
            form.setRowVisible(row, False)
        if authentication:
            self.setWindowTitle('Steam Depot Downloader authentication — Playlite')
            self.start_button.setText('Authenticate')
            notice.setText('Manage Moon, Hubcap and Steam authentication here. Login sessions are saved in KWallet. Connect the VPN in plugin settings first.')
            if authentication == 'steam':
                for index in range(form.rowCount()):
                    form.setRowVisible(index, False)
                self.setWindowTitle('Steam login — Playlite')
                self.resize(560, 300)
                self.pending_password = ''
                self.login_succeeded = False
                self.steam_form = QFormLayout()
                self.steam_form.setVerticalSpacing(12)
                notice = QLabel('Sign in to your Steam download account.')
                self.steam_form.addRow(notice)
                self.username.setParent(self)
                self.steam_form.addRow('Account name', self.username)
                self.password = QLineEdit()
                self.password.setEchoMode(QLineEdit.EchoMode.Password)
                self.password.returnPressed.connect(self.download)
                self.steam_form.addRow('Password', self.password)
                self.response.setParent(self)
                self.send.setParent(self)
                self.send.setText('Continue')
                self.guard_row = QHBoxLayout()
                self.guard_row.addWidget(self.response); self.guard_row.addWidget(self.send)
                self.response.setPlaceholderText('Steam Guard code')
                self.steam_form.addRow('Steam Guard', self.guard_row)
                self.steam_form.setRowVisible(self.guard_row, False)
                self.status.setParent(self)
                self.status.setText('Your login will be saved securely in KWallet.')
                self.steam_form.addRow(self.status)
                self.vpn_button = QPushButton('Connect VPN')
                self.vpn_button.clicked.connect(self.connect_vpn)
                self.steam_form.addRow(self.vpn_button)
                self.steam_form.setRowVisible(self.vpn_button, False)
                details = QPushButton('Show details')
                details.setCheckable(True)
                self.log.setParent(self); self.log.setMaximumHeight(150)
                self.steam_form.addRow(self.log)
                self.steam_form.setRowVisible(self.log, False)
                def toggle_details(visible):
                    self.steam_form.setRowVisible(self.log, visible)
                    details.setText('Hide details' if visible else 'Show details')
                    self.adjustSize()
                details.toggled.connect(toggle_details)
                self.start_button.setParent(self); self.start_button.setText('Authenticate')
                self.cancel.setParent(self)
                footer = QHBoxLayout(); footer.setSpacing(10)
                footer.addWidget(details); footer.addStretch()
                footer.addWidget(self.start_button); footer.addWidget(self.cancel)
                self.steam_form.addRow(footer)
                form.addRow(self.steam_form)
                for control in (self.username, self.status, self.start_button, self.cancel):
                    control.show()
        else:
            notice.setText('Select a game, provider, depot and download folder. VPN connection and saved authentication are configured in plugin settings.')

    def connect_vpn(self):
        from .credentials import Wallet
        from .settings import Preferences
        def operation():
            settings = QSettings('Playlite', 'SteamDownloader')
            preferences = Preferences(settings.value('country', ''), settings.value('protocol', 'udp')).validate()
            self.network.cancelled.clear()
            return self.network.connect(preferences, *Wallet().read())
        def done(message):
            self.status.setText(message)
            self.steam_form.setRowVisible(self.vpn_button, False)
        self.task(operation, done)

    def save_key(self):
        key = self.key.text().strip()
        if not key or any(c in key for c in '\r\n\0'):
            self.status.setText('Enter a valid Hubcap API key.'); return
        self.key.clear()
        self.task(lambda: HubcapKeyWallet().save({'key':key}), lambda _: self.status.setText('Hubcap API key saved in KWallet.'))

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select empty download folder', self.destination.text())
        if folder: self.destination.setText(folder)

    def task(self, operation, completed):
        if self.busy: return
        from .plugin import Job
        self.busy = True; self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        result = []
        def work():
            result.append(operation())
            return 'Done.'
        job = Job(work); self.jobs.add(job)
        def done(message):
            self.busy = False; self.jobs.discard(job)
            self.fetch.setEnabled(True); self.start_button.setEnabled(True)
            if result:
                try: completed(result[0])
                except Exception as error: self.status.setText(str(error))
            else: self.status.setText(message)
        job.signals.finished.connect(done)
        self.status.setText('Working through the isolated VPN…')
        QThreadPool.globalInstance().start(job)

    def login(self):
        code = self.code.text(); self.code.clear()
        self.task(lambda: self.moon.login(code), self.status.setText)

    def fetch_pack(self):
        try:
            app = int(self.appid.text())
            source = self.provider.currentText()
            credential = self.key.text() if source == 'Hubcap' else ''
        except Exception as error:
            self.status.setText(str(error)); return
        def done(rows):
            if source == 'Hubcap': self.network.hubcap_confirmed = True
            self.rows = rows; self.pack_app = app
            self.depot.clear()
            for row in rows: self.depot.addItem(f'{row.id} / {row.manifest}')
            self.status.setText(f'{len(rows)} depot manifest(s) ready. Steam will authenticate independently.')
        self.task(lambda: self.transport.fetch(source, app, self.moon.token() if source == 'Luie' else (HubcapKeyWallet().read() or {}).get('key', '') if source == 'Hubcap' else credential), done)

    def download(self):
        if self.busy: return
        stage = None
        if self.authentication == 'steam':
            try:
                self.network.check()
            except Exception:
                self.status.setText('Connect the isolated VPN before signing in.')
                self.steam_form.setRowVisible(self.vpn_button, True)
                return
            self.pending_password = self.password.text()
            self.password.clear()
            self.steam_form.setRowVisible(self.guard_row, False)
        try:
            if not self.authentication:
                app = int(self.appid.text())
                if not self.rows or app != self.pack_app: raise ValueError('Fetch the manifest pack for this App ID first.')
                destination = Path(self.destination.text()).expanduser().resolve()
                if not self.destination.text() or not destination.is_dir() or any(destination.iterdir()):
                    raise ValueError('Choose an existing empty download folder.')
            if not self.username.text().strip(): raise ValueError('Enter your Steam account name.')
            row = self.rows[self.depot.currentIndex()] if not self.authentication else None
            self.temporary = tempfile.TemporaryDirectory(prefix='playlite-depot-input-')
            pack = None
            if row is not None:
                pack = Path(self.temporary.name) / 'pack'; prepare_depot(row, pack)
            self.steam_wallet = SteamSessionWallet(self.username.text())
            saved = self.steam_wallet.read()
            if not self.authentication and saved is None:
                raise ValueError('Sign into Steam in plugin settings → Steam login before downloading.')
            auth = Path(self.temporary.name) / 'auth'
            auth.mkdir(mode=0o777); auth.chmod(0o777)
            self.auth_file = auth / 'account.config'
            if saved is not None:
                data = base64.b64decode(saved['data'], validate=True)
                if len(data) > 4 * 1024 * 1024: raise ValueError('Saved Steam session exceeds the size limit.')
                self.auth_file.write_bytes(data); self.auth_file.chmod(0o666)
            # Keep incomplete output in a separate staging folder, never overwrite files.
            stage = Path(self.temporary.name) / 'output' if self.authentication else destination / '.playlite-download'
            stage.mkdir(mode=0o777); stage.chmod(0o777)
            args = worker_args(self.network, Request(app, row.id, row.manifest) if row is not None else Request(1, 1), stage, pack=pack, username=self.username.text())
            args[args.index('--workdir') + 1] = '/auth'
            index = args.index('playlite-depot-worker:test')
            args[index:index] = ['--mount', f'type=bind,src={auth},dst=/auth']
            if self.authentication:
                import secrets
                index = args.index('playlite-depot-worker:test')
                args[index+1:] = ['-login-only', '-username', self.username.text().strip(), '-loginid', str(secrets.randbelow(2**32-1)+1)]
            args.append('-remember-password')
            self.container = 'playlite-download-' + uuid.uuid4().hex
            args[1:1] = ['--name', self.container]
            # Output must remain writable by the desktop user after UID 65534 exits.
            args[args.index('--entrypoint') + 1] = 'python3'
            index = args.index('playlite-depot-worker:test')
            args[index + 1:index + 1] = ['-c', "import os,sys; os.umask(0); os.execv('/tool/DepotDownloaderMod', ['/tool/DepotDownloaderMod', *sys.argv[1:]])"]
        except Exception as error:
            if self.temporary: self.temporary.cleanup(); self.temporary = None
            if stage is not None:
                try: stage.rmdir()  # Only remove an empty staging folder created by this attempt.
                except OSError: pass
            if self.authentication == 'steam': self.pending_password = ''
            self.status.setText(str(error)); return
        QSettings('Playlite', 'SteamDownloader').setValue('steam_account', self.username.text().strip())
        self.busy = True; self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        self.cancel.setText('Cancel login' if self.authentication else 'Cancel download')
        self.output = ''; self.auth_needed = False; self.log.clear()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start('docker', args)
        self.status.setText('Signing in to Steam…' if self.authentication else 'Downloading using your saved Steam session. Incomplete files remain in .playlite-download.')

    def read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        self.output += text
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()
        if self.authentication:
            if 'Please enter your 2 factor auth code' in text or 'Please enter the authentication code' in text:
                self.status.setText('Enter your Steam Guard code from your authenticator app.' if '2 factor' in text else 'Enter the Steam Guard code sent to your email.')
                if self.authentication == 'steam':
                    self.steam_form.setRowVisible(self.guard_row, True)
                    self.response.setFocus()
            elif 'Enter account password' in text:
                if self.authentication == 'steam' and self.pending_password:
                    self.process.write((self.pending_password + '\n').encode())
                    self.pending_password = ''
                else:
                    self.status.setText('Enter your Steam password below.')
                    if self.authentication == 'steam':
                        self.steam_form.setRowVisible(self.guard_row, True)
                        self.response.setPlaceholderText('Steam password')
            elif 'Use the Steam Mobile App' in text:
                self.status.setText('Approve this sign-in in the Steam mobile app. Waiting for approval…')
                if self.authentication == 'steam':
                    self.steam_form.setRowVisible(self.guard_row, False)
        prompts = ('Enter account password', 'Please enter your 2 factor auth code', 'Please enter the authentication code')
        if not self.authentication and not getattr(self, 'auth_needed', False) and any(prompt in self.output for prompt in prompts):
            self.auth_needed = True
            self.close_or_cancel()

    def send_response(self):
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.process.write((self.response.text() + '\n').encode())
        self.response.clear()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.finished(1)

    def finished(self, code, *_):
        self.read_output()
        try:
            if self.authentication:
                if code: raise RuntimeError('Steam authentication failed or was cancelled. See the login log.')
                if not self.auth_file.is_file(): raise RuntimeError('Steam did not create a reusable session. See the login log.')
                self.network.steam_confirmed = True
                self.status.setText('Steam authentication completed.')
            else:
                require_download_success(code, self.output)
                self.status.setText('Depot download completed and validated. Files are in .playlite-download; automatic installation is not enabled yet.')
        except Exception as error: self.status.setText(str(error) + ('' if self.authentication else ' Incomplete files are retained for inspection.'))
        if self.temporary:
            try:
                if self.auth_file.is_file():
                    data = self.auth_file.read_bytes()
                    if len(data) > 4 * 1024 * 1024: raise ValueError('Steam session exceeds the size limit.')
                    self.steam_wallet.save({'data':base64.b64encode(data).decode()})
            except Exception:
                self.status.setText(self.status.text() + ' Steam login could not be saved in KWallet.')
            finally:
                self.temporary.cleanup(); self.temporary = None
        self.busy = False; self.fetch.setEnabled(True); self.start_button.setEnabled(True)
        self.cancel.setText('Close')
        if self.authentication == 'steam':
            self.pending_password = ''
            self.steam_form.setRowVisible(self.guard_row, False)
            self.login_succeeded = code == 0 and 'could not be saved' not in self.status.text() and self.status.text() == 'Steam authentication completed.'
            self.network.steam_confirmed = self.login_succeeded
            if self.login_succeeded:
                self.status.setText(f'Signed in as {self.username.text().strip()}.')
                self.steam_form.setRowVisible(self.password, False)
                self.start_button.hide()
                self.cancel.setText('Done')
            else:
                self.start_button.setText('Try again')

        if getattr(self, 'auth_needed', False):
            self.status.setText('Steam requires a new login. Open plugin settings → Steam login and sign in again. Incomplete files were retained.')

    def forget_steam(self):
        if self.busy: return
        if not self.username.text().strip():
            self.status.setText('Enter the Steam account name whose saved login you want to remove.'); return
        username = self.username.text()
        self.network.steam_confirmed = False
        self.task(lambda: SteamSessionWallet(username).clear(), lambda _: self.status.setText('Saved Steam login removed from KWallet.'))

    def close_or_cancel(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.status.setText('Stopping the download worker…')
            self.stopper = QProcess(self)
            self.stopper.start('docker', ['rm', '-f', self.container])
        elif not self.busy: self.close()

    def closeEvent(self, event):
        if self.busy:
            self.close_or_cancel(); event.ignore(); return
        self.moon.session = None
        self.rows = []
        self.key.clear(); self.response.clear()
        if self.authentication == 'steam':
            self.password.clear(); self.pending_password = ''
        super().closeEvent(event)
