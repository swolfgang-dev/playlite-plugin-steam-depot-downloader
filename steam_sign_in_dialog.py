"""Monitor Steam sign-in in a dedicated VM viewer and return to setup."""
import json
import time
from PyQt6.QtCore import QTimer,QThreadPool,QSettings
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPushButton


class SteamSignInDialog(QDialog):
    def __init__(self,network,parent=None):
        super().__init__(parent);self.network=network;self.job=None;self.viewer=None;self.closing=False
        self.setWindowTitle('Steam sign-in — Playlite');self.resize(580,260)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
        text=QLabel('Sign into Steam in the VM window. Approve Steam Guard if requested and keep Remember me enabled. When sign-in completes, the viewer closes and setup continues to LuaTools and Hubcap.')
        text.setWordWrap(True);layout.addWidget(text)
        self.status=QLabel('Starting Steam…');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.start_button=QPushButton('Open / retry Steam VM');self.start_button.clicked.connect(self.start_steam);layout.addWidget(self.start_button)
        self.close_button=QPushButton('Return to setup');self.close_button.clicked.connect(self.reject);layout.addWidget(self.close_button)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.refresh)
        QTimer.singleShot(0,lambda:self.start_steam() if self.isVisible() else None)
    def close_viewer(self):
        viewer=self.viewer;self.viewer=None
        if viewer is not None and viewer.poll() is None:viewer.terminate()
    def start_steam(self,checked=False):
        if self.job is not None:return
        from .plugin import Job
        from .settings import Preferences
        from .steam_vm_viewer import open_viewer
        settings=QSettings('Playlite','SteamDownloader')
        preferences=Preferences(settings.value('country',''),settings.value('protocol','tcp')).validate()
        self.timer.stop();self.close_viewer();self.start_button.setEnabled(False)
        self.status.setText('Starting the VM and Steam…')
        def start():
            self.network.cancelled.clear()
            for attempt in range(3):
                try:self.network.connect(preferences,progress=job.signals.progress.emit);break
                except RuntimeError:
                    if attempt==2:raise
                    job.signals.progress.emit('Waiting for NordVPN to finish starting…');time.sleep(3)
            state=self.network.agent.rpc({'command':'status'})
            if not state.get('steam_ready') or not state.get('moon_installed'):
                from .vm_setup import provision_vm
                provision_vm(self.network,preferences,job.signals.progress.emit)
            self.network.agent.rpc({'command':'start'})
            state=self.network.agent.rpc({'command':'status'})
            if not state.get('steam_session_saved'):
                self.viewer=open_viewer(self.network,job.signals.progress.emit)
            return json.dumps({'started':True})
        job=Job(start);self.job=job;job.signals.progress.connect(self.status.setText)
        def finished(message):
            self.job=None;self.start_button.setEnabled(True)
            if self.closing:self.close_viewer();return
            try:
                if not json.loads(message).get('started'):raise ValueError()
                self.status.setText('Waiting for Steam sign-in in the VM window…');self.timer.start();self.refresh()
            except (ValueError,TypeError,AttributeError):self.status.setText(message)
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)
    def refresh(self):
        if self.job is not None or self.closing:return
        from .plugin import Job
        job=Job(lambda:json.dumps(self.network.agent.rpc({'command':'status'})));self.job=job
        def finished(message):
            self.job=None
            if self.closing:return
            try:
                result=json.loads(message)
                if result.get('steam_running') and result.get('steam_session_saved'):
                    self.accept();return
                if self.viewer is not None and self.viewer.poll() is not None:
                    self.status.setText('VM viewer closed. Choose Open / retry Steam VM to continue sign-in.')
            except (ValueError,TypeError,AttributeError):self.status.setText(message)
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)
    def done(self,result):
        self.closing=True;self.timer.stop();self.close_viewer();super().done(result)
    def closeEvent(self,event):
        self.closing=True;self.timer.stop();self.close_viewer();super().closeEvent(event)
