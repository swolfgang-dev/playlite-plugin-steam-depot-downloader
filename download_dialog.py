"""Explicit manifest and Steam login workflow; never uses a host Steam profile."""
import tempfile
import uuid
from pathlib import Path
from PyQt6.QtCore import QProcess, QThreadPool
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QPushButton,
                            QLabel, QPlainTextEdit, QFileDialog, QHBoxLayout)
from .moon import Moon
from .providers import SOURCES, Transport, prepare_depot
from .worker import Request, worker_args, require_download_success

class DownloadDialog(QDialog):
    def __init__(self, network, parent=None):
        super().__init__(parent)
        self.network = network
        self.transport = Transport(network)
        self.moon = Moon(self.transport)
        self.rows = []
        self.jobs = set()
        self.process = None
        self.temporary = None
        self.container = None
        self.output = ''
        self.setWindowTitle('Steam Depot Downloader — Playlite')
        self.resize(850, 720)
        form = QFormLayout(self)
        notice = QLabel('Downloads use a separate Steam session inside the VPN. Use an account that owns the game. Moon login lasts until this window closes. Download one selected depot at a time; the destination must be empty.')
        notice.setWordWrap(True)
        form.addRow(notice)
        self.appid = QLineEdit('736260')
        form.addRow('Steam App ID', self.appid)
        self.provider = QComboBox(); self.provider.addItems(SOURCES)
        form.addRow('Manifest provider', self.provider)
        self.code = QLineEdit(); self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setPlaceholderText('Get a login code from lua.tools')
        login = QPushButton('Sign into Moon'); login.clicked.connect(self.login)
        line = QHBoxLayout(); line.addWidget(self.code); line.addWidget(login)
        form.addRow('Moon login code', line)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('Hubcap API key', self.key)
        self.fetch = QPushButton('Fetch manifest pack'); self.fetch.clicked.connect(self.fetch_pack)
        form.addRow(self.fetch)
        self.depot = QComboBox(); form.addRow('Depot / manifest', self.depot)
        self.username = QLineEdit(); form.addRow('Steam account name', self.username)
        self.destination = QLineEdit()
        browse = QPushButton('Browse…'); browse.clicked.connect(self.browse)
        line = QHBoxLayout(); line.addWidget(self.destination); line.addWidget(browse)
        form.addRow('Download folder', line)
        self.start_button = QPushButton('Download selected depot'); self.start_button.clicked.connect(self.download)
        form.addRow(self.start_button)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); form.addRow(self.log)
        self.response = QLineEdit(); self.response.setEchoMode(QLineEdit.EchoMode.Password)
        self.response.setPlaceholderText('Enter Steam password or Steam Guard code when requested in the log')
        self.send = QPushButton('Send securely'); self.send.clicked.connect(self.send_response)
        self.response.returnPressed.connect(self.send_response)
        line = QHBoxLayout(); line.addWidget(self.response); line.addWidget(self.send)
        form.addRow('Steam login response', line)
        self.status = QLabel('Connect the VPN and build the native worker image before continuing.')
        self.status.setWordWrap(True); form.addRow(self.status)
        self.cancel = QPushButton('Close'); self.cancel.clicked.connect(self.close_or_cancel)
        form.addRow(self.cancel)
        self.busy = False

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
            self.rows = rows; self.pack_app = app
            self.depot.clear()
            for row in rows: self.depot.addItem(f'{row.id} / {row.manifest}')
            self.status.setText(f'{len(rows)} depot manifest(s) ready. Steam will authenticate independently.')
        self.task(lambda: self.transport.fetch(source, app, self.moon.token() if source == 'Luie' else credential), done)

    def download(self):
        if self.busy: return
        stage = None
        try:
            app = int(self.appid.text())
            if not self.rows or app != self.pack_app: raise ValueError('Fetch the manifest pack for this App ID first.')
            destination = Path(self.destination.text()).expanduser().resolve()
            if not self.destination.text() or not destination.is_dir() or any(destination.iterdir()):
                raise ValueError('Choose an existing empty download folder.')
            if not self.username.text().strip(): raise ValueError('Enter your Steam account name.')
            row = self.rows[self.depot.currentIndex()]
            self.temporary = tempfile.TemporaryDirectory(prefix='playlite-depot-input-')
            pack = Path(self.temporary.name) / 'pack'; prepare_depot(row, pack)
            # Keep incomplete output in a separate staging folder, never overwrite files.
            stage = destination / '.playlite-download'
            stage.mkdir(mode=0o777); stage.chmod(0o777)
            args = worker_args(self.network, Request(app, row.id, row.manifest), stage, pack=pack, username=self.username.text())
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
            self.status.setText(str(error)); return
        self.busy = True; self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        self.cancel.setText('Cancel download')
        self.output = ''; self.log.clear()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start('docker', args)
        self.status.setText('Watch the log for Steam password and Steam Guard prompts. Incomplete files remain in .playlite-download.')

    def read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        self.output += text
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()

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
            require_download_success(code, self.output)
            self.status.setText('Depot download completed and validated. Files are in .playlite-download; automatic installation is not enabled yet.')
        except Exception as error: self.status.setText(str(error) + ' Incomplete files are retained for inspection.')
        if self.temporary: self.temporary.cleanup(); self.temporary = None
        self.busy = False; self.fetch.setEnabled(True); self.start_button.setEnabled(True)
        self.cancel.setText('Close')

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
        super().closeEvent(event)
