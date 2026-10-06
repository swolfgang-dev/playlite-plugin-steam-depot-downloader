"""First-stage isolated Steam login and Moon setup interface."""
import socket
import subprocess
from PyQt6.QtCore import QThreadPool,QTimer,QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton
from .steam_runtime import SteamRuntime

class SteamRuntimeDialog(QDialog):
    def __init__(self,network,parent=None):
        super().__init__(parent)
        self.runtime=SteamRuntime(network);self.jobs=set();self.relay=None
        self.setWindowTitle('Isolated Steam setup — Playlite');self.resize(640,270)
        layout=QVBoxLayout(self)
        intro=QLabel('Start the isolated environment, then open its desktop to sign into Steam. Once Steam has finished updating, install LuaMoon. This uses a separate Steam login and library.')
        intro.setWordWrap(True);layout.addWidget(intro)
        self.status=QLabel('Connect NordVPN first.');self.status.setWordWrap(True);layout.addWidget(self.status)
        controls=QHBoxLayout();controls.setSpacing(10)
        for text,callback in [('Start environment',lambda:self.task(self.runtime.start)),('Open desktop',self.desktop),('Refresh status',self.refresh),('Install LuaMoon',lambda:self.task(lambda:self.runtime.request('install_moon')))]:
            button=QPushButton(text);button.clicked.connect(callback);controls.addWidget(button)
        layout.addLayout(controls)
        close=QPushButton('Close');close.clicked.connect(self.close);layout.addWidget(close)

    def task(self,operation):
        if self.jobs:return
        from .plugin import Job
        def run():
            result=operation()
            if isinstance(result,dict):
                return f'Steam: {"running" if result["steam_running"] else "stopped"} · LuaMoon: {"installing" if result["moon_installing"] else "installed" if result["moon_installed"] else "not installed"}'
            return str(result)
        job=Job(run);self.jobs.add(job);self.status.setText('Working…')
        def finished(message):self.jobs.discard(job);self.status.setText(message)
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)

    def refresh(self):self.task(lambda:self.runtime.request('status'))

    def desktop(self):
        if self.jobs:return
        if not (self.runtime.root/'control/web.sock').exists():
            self.status.setText('Start the environment first.');return
        if self.relay is None or self.relay.poll() is not None:
            with socket.socket() as connection:
                connection.bind(('127.0.0.1',0));self.port=connection.getsockname()[1]
            self.relay=subprocess.Popen(['socat',f'TCP-LISTEN:{self.port},bind=127.0.0.1,reuseaddr,fork',f'UNIX-CONNECT:{self.runtime.root}/control/web.sock'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.task(lambda:self.runtime.request('start'))
        QTimer.singleShot(500,lambda:QDesktopServices.openUrl(QUrl(f'http://127.0.0.1:{self.port}/vnc.html?autoconnect=1&resize=scale')))

    def closeEvent(self,event):
        if self.jobs:event.ignore();return
        if self.relay and self.relay.poll() is None:self.relay.terminate()
        from .vpn_lifecycle import disconnect_when_idle
        disconnect_when_idle(self.runtime.network)
        super().closeEvent(event)
