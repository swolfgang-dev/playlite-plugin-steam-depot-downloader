"""Desktop-free depot worker setup and account controls."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PyQt6.QtCore import QSettings, QThreadPool, Qt
from PyQt6 import sip
from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QFormLayout, QLabel,
                            QPushButton, QLineEdit, QComboBox, QCheckBox, QFileDialog, QScrollArea, QGroupBox, QHBoxLayout, QSizePolicy)
from .tools.build_worker import REVISION
from .credentials import Wallet
from .settings import Preferences

WORKER_IMAGE = 'playlite-depot-worker:test'


def require_worker():
    try:
        result=subprocess.run(['docker','image','inspect',WORKER_IMAGE],capture_output=True,text=True,timeout=30)
        if result.returncode:raise ValueError('missing')
        info=json.loads(result.stdout)[0]
        if info['Config'].get('Labels',{}).get('io.playlite.depot-worker.revision')!=REVISION:
            raise ValueError('outdated')
    except (OSError,subprocess.TimeoutExpired,ValueError,KeyError,IndexError,TypeError):
        raise RuntimeError('Build the depot worker in Settings → Plugins → Steam Downloader → Build worker.') from None
    return 'Depot worker is ready.'


def build_worker(progress):
    if not shutil.which('docker') or not shutil.which('git'):
        raise RuntimeError('Docker and Git are required. Install them before building the worker.')
    progress('Building the depot worker with a temporary .NET SDK container…')
    script=Path(__file__).parent/'tools'/'build_worker.py'
    # Sources, SDK/NuGet state and publish output are disposable. Only Docker
    # images remain; no host SDK, Steam client or VM is installed.
    with tempfile.TemporaryDirectory(prefix='playlite-worker-build-') as work:
        result=subprocess.run([sys.executable,str(script),'--work',work,'--container-sdk'],
                              capture_output=True,text=True,timeout=1800)
        if result.returncode:
            raise RuntimeError('Worker build failed: '+(result.stderr or result.stdout)[-3000:])
    return require_worker()


class DepotSettings(QWidget):
    def __init__(self,network,parent=None):
        super().__init__(parent)
        self.network=network
        self.jobs=set();self.busy=False;self.settings_closed=False
        self.settings=QSettings('Playlite','SteamDownloader')
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0);layout.setSpacing(14)
        note=QLabel('Download Steam games through an isolated VPN. Set up the download service, connect NordVPN, then sign in below.')
        note.setWordWrap(True);layout.addWidget(note)
        self.buttons=[]

        def section(title):
            box=QGroupBox(title)
            form=QFormLayout(box)
            form.setContentsMargins(16,20,16,14)
            form.setHorizontalSpacing(16);form.setVerticalSpacing(10)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            layout.addWidget(box)
            return form

        def action(label,callback):
            button=QPushButton(label);button.clicked.connect(callback)
            button.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            self.buttons.append(button)
            return button

        def actions(*buttons):
            row=QHBoxLayout();row.setSpacing(8)
            for button in buttons:row.addWidget(button)
            row.addStretch()
            return row

        form=section('Download service')
        description=QLabel('One-time setup. Requires Docker and Git; credentials are stored in KWallet.')
        description.setWordWrap(True);form.addRow(description)
        form.addRow(actions(action('Build worker',lambda:self.run(build_worker)),
                            action('Check installation',lambda:self.run(lambda progress:require_worker()))))

        form=section('NordVPN')
        form.addRow('Service account',actions(action('Manage credentials…',lambda:self.authenticate('NordVPN'))))
        form.addRow('Connection',actions(action('Connect',self.connect_vpn),
                     action('Check connection',lambda:self.run(lambda progress:self.network.check())),
                     action('Disconnect',self.disconnect_vpn)))
        self.keep_vpn=QCheckBox('Keep VPN connected while Playlite is open')
        self.keep_vpn.setChecked(self.settings.value('keep_vpn_open',False,type=bool))
        self.keep_vpn.toggled.connect(self.save);form.addRow(self.keep_vpn)
        options=QCheckBox('Connection options')
        form.addRow(options)
        self.country=QLineEdit(self.settings.value('country',''))
        self.country.setPlaceholderText('Automatic recommended server')
        self.protocol=QComboBox();self.protocol.addItems(['udp','tcp'])
        self.protocol.setCurrentText(self.settings.value('protocol','udp'))
        self.protocol.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
        self.country.editingFinished.connect(self.save)
        self.protocol.currentTextChanged.connect(self.save)
        form.addRow('Country',self.country);form.addRow('Protocol',self.protocol)
        form.setRowVisible(self.country,False);form.setRowVisible(self.protocol,False)
        def show_options(visible):
            form_options.setRowVisible(self.country,visible)
            form_options.setRowVisible(self.protocol,visible)
        form_options=form
        options.toggled.connect(show_options)

        form=section('Accounts and manifests')
        for name,provider,detail,label in (
                ('Steam','Steam','Account for downloading owned content','Sign in…'),
                ('LuaTools','Moon','Use a fresh Discord /login code','Sign in…'),
                ('Hubcap','Hubcap','Use your manifest API key','Set API key…')):
            row=QHBoxLayout();row.setSpacing(12)
            hint=QLabel(detail);hint.setWordWrap(True)
            row.addWidget(hint,1)
            row.addWidget(action(label,lambda checked=False,provider=provider:self.authenticate(provider)))
            form.addRow(name,row)
        self.provider=QComboBox()
        self.provider.addItem('Hubcap','Hubcap');self.provider.addItem('LuaTools','Luie')
        self.provider.setCurrentIndex(1 if self.settings.value('manifest_provider','Hubcap')=='Luie' else 0)
        self.provider.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
        self.provider.currentIndexChanged.connect(self.save)
        form.addRow('Preferred provider',self.provider)

        form=section('Downloads')
        self.root=QLineEdit(self.settings.value('download_root',''))
        self.root.setPlaceholderText('Use General installation folder')
        self.root.editingFinished.connect(self.save)
        folder=QHBoxLayout();folder.setSpacing(8);folder.addWidget(self.root,1)
        folder.addWidget(action('Browse…',self.choose_root))
        form.addRow('Save games to',folder)
        self.status=QLabel('Ready to set up. Build the worker first, then connect the VPN.')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        layout.addStretch()
        owner=self.window()
        if isinstance(owner,QDialog):owner.finished.connect(self.closed)

    def closed(self,*_):
        self.settings_closed=True;self.save()
        if not self.busy:
            from .vpn_lifecycle import disconnect_when_idle
            disconnect_when_idle(self.network)

    def shutdown_pending(self):return self.busy or bool(self.jobs)

    def request_shutdown(self):self.network.cancel()

    def save(self,*_):
        self.settings.setValue('country',self.country.text().strip())
        self.settings.setValue('protocol',self.protocol.currentText())
        if hasattr(self,'provider'):self.settings.setValue('manifest_provider',self.provider.currentData())
        if hasattr(self,'root'):self.settings.setValue('download_root',self.root.text().strip())
        if hasattr(self,'keep_vpn'):self.settings.setValue('keep_vpn_open',self.keep_vpn.isChecked())

    def choose_root(self):
        path=QFileDialog.getExistingDirectory(self,'Download folder',self.root.text())
        if path:self.root.setText(path);self.save()

    def run(self,operation):
        if self.busy:return
        from .plugin import Job
        self.busy=True
        for button in self.buttons:button.setEnabled(False)
        job=Job(lambda:operation(job.signals.progress.emit));self.jobs.add(job)
        job.signals.progress.connect(self.status.setText)
        def done(message):
            self.busy=False;self.jobs.discard(job)
            if not sip.isdeleted(self):
                self.status.setText(str(message))
                for button in self.buttons:button.setEnabled(True)
            if sip.isdeleted(self) or self.settings_closed:
                from .vpn_lifecycle import disconnect_when_idle
                disconnect_when_idle(self.network)
        job.signals.finished.connect(done)
        self.status.setText('Working…');QThreadPool.globalInstance().start(job)

    def connect_vpn(self):
        self.save()
        def operation(progress):
            require_worker()
            if self.network.container:return self.network.check()
            self.network.cancelled.clear()
            return self.network.connect(Preferences(self.country.text().strip(),self.protocol.currentText()).validate(),
                                        *Wallet().read(),progress=progress)
        self.run(operation)

    def disconnect_vpn(self):
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.network):
            self.status.setText('Pause or cancel queued and active downloads before disconnecting.');return
        self.run(lambda progress:self.network.disconnect() or 'VPN disconnected.')

    def authenticate(self,provider):
        if self.busy:return
        if provider=='Steam':
            from .download_dialog import DownloadDialog
            DownloadDialog(self.network,self,authentication='steam').exec()
        else:
            from .authentication_dialog import CredentialDialog
            CredentialDialog(provider,self.network,self).exec()


class DepotSetupDialog(QDialog):
    def __init__(self,network,parent=None):
        super().__init__(parent)
        self.setWindowTitle('Steam Downloader setup — Playlite');self.resize(580,680)
        layout=QVBoxLayout(self);self.panel=DepotSettings(network,self)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.panel)
        layout.addWidget(scroll)
        close=QPushButton('Done');close.clicked.connect(self.accept);layout.addWidget(close)

    def accept(self):
        if not self.panel.busy:super().accept()

    def reject(self):
        if not self.panel.busy:super().reject()

    def closeEvent(self,event):
        if self.panel.busy:event.ignore()
        else:super().closeEvent(event)
