"""Provision and open the isolated Steam desktop."""
import socket
import subprocess
from PyQt6.QtCore import QThreadPool,QTimer,QUrl,QSettings
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit
from .steam_runtime import SteamRuntime

class SteamRuntimeDialog(QDialog):
    def __init__(self,network,parent=None):
        super().__init__(parent)
        self.runtime=SteamRuntime(network);self.jobs=set();self.relay=None
        self.setWindowTitle('Isolated Steam — Playlite');self.resize(680,400)
        layout=QVBoxLayout(self)
        intro=QLabel('Setup installs the private Steam desktop and LuaMoon using your saved NordVPN credentials. Sign into Steam and your providers inside that desktop. Existing sessions and game files are retained.')
        intro.setWordWrap(True);layout.addWidget(intro)
        authenticate=QPushButton('Authenticate NordVPN…')
        def credentials():
            from .authentication_dialog import CredentialDialog
            CredentialDialog('NordVPN',self.runtime.network,self).exec()
        authenticate.clicked.connect(credentials);layout.addWidget(authenticate)
        self.status=QLabel('Ready to set up isolated Steam.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.document().setMaximumBlockCount(500);layout.addWidget(self.log)
        controls=QHBoxLayout();controls.setSpacing(10);self.buttons=[]
        for text,callback in [('Set up isolated Steam',self.install),('Open desktop',self.desktop),('Refresh status',self.refresh)]:
            button=QPushButton(text);button.clicked.connect(callback);controls.addWidget(button);self.buttons.append(button)
        layout.addLayout(controls)
        self.close_button=QPushButton('Close');self.close_button.clicked.connect(self.close);layout.addWidget(self.close_button)

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
        for button in [*self.buttons,self.close_button]:button.setEnabled(False)
        job.signals.progress.connect(self.log.appendPlainText)
        def finished(message):
            self.jobs.discard(job);self.status.setText(message);self.log.appendPlainText(message)
            for button in [*self.buttons,self.close_button]:button.setEnabled(True)
            if result and after:after()
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)

    def install(self):
        if self.jobs:return
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.runtime.network):
            self.status.setText('Wait for queued downloads to finish before running setup.');return
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

    def refresh(self):self.task(lambda progress:self.runtime.request('status'))

    def desktop(self):
        def start(progress):
            from .steam_setup import wait_for
            self.runtime.start()
            wait_for(self.runtime,lambda state:True,90,'The isolated desktop did not start. Run setup first.')
            self.runtime.request('start')
            return 'Steam and provider authentication are available in the isolated desktop.'
        self.task(start,self.open_desktop)

    def open_desktop(self):
        shared=vars(self.runtime.network).get('_steam_desktop_relay')
        if shared and shared[0].poll() is None:
            self.relay,self.port=shared
        if self.relay is None or self.relay.poll() is not None:
            with socket.socket() as connection:
                connection.bind(('127.0.0.1',0));self.port=connection.getsockname()[1]
            try:
                self.relay=subprocess.Popen(['socat',f'TCP-LISTEN:{self.port},bind=127.0.0.1,reuseaddr,fork',f'UNIX-CONNECT:{self.runtime.root}/control/web.sock'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except OSError:
                self.status.setText('Install socat to open the isolated desktop.');return
        self.runtime.network._steam_desktop_relay=(self.relay,self.port)
        QTimer.singleShot(500,lambda:QDesktopServices.openUrl(QUrl(f'http://127.0.0.1:{self.port}/vnc.html?autoconnect=1&resize=scale')))

    def closeEvent(self,event):
        if self.jobs:event.ignore();return
        from .vpn_lifecycle import disconnect_when_idle
        # Settings and downloader own their connection until their own close
        # handlers release it. A child setup popup must not release that VPN.
        owner=self.parentWidget()
        while owner is not None:
            if (hasattr(owner,'settings_closed') and not owner.settings_closed) or getattr(owner,'network',None) is self.runtime.network:
                break
            owner=owner.parentWidget()
        if owner is None:disconnect_when_idle(self.runtime.network)
        super().closeEvent(event)
