"""Workshop access through the configured VM's local QEMU guest agent."""
from pathlib import Path
import time
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton

from .vm_backend import VMNetwork, VMSteamRuntime


class WorkshopVM:
    def __init__(self, network=None):
        self.network=network or VMNetwork()
        self.runtime=VMSteamRuntime(self.network)

    def start(self):
        from .steam_queue_runner import ensure_vpn
        from PyQt6.QtCore import QSettings
        settings=QSettings('Playlite','SteamDownloader')
        ensure_vpn(self.network,settings.value('country',''),settings.value('protocol','tcp'),lambda _:None)
        self.runtime.start()
        return self.runtime.request('start')

    def request(self,command,appid=None,item=None):
        return self.runtime.request(command,appid,**({'item':item} if item is not None else {}))

    def prepare(self, app, progress):
        progress('Starting Steam inside the VM…')
        self.start()
        deadline = time.monotonic() + 90
        while True:
            try:
                self.request('authentication_status')
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('LuaMoon is not ready. Open the VM and sign into Steam and LuaMoon.')
                time.sleep(2)
        if not self.request('has_game', app).get('exists'):
            progress('Adding game through LuaMoon…')
            self.request('add', app)
            deadline = time.monotonic() + 180
            while True:
                state = self.request('add_status', app).get('state', {})
                if state.get('status') == 'done':
                    break
                if state.get('status') in ('failed', 'cancelled'):
                    raise RuntimeError(state.get('error') or 'LuaMoon could not add the game.')
                if time.monotonic() >= deadline:
                    raise RuntimeError('LuaMoon preparation timed out; check the VM before retrying.')
                time.sleep(1)
        self.request('workshop', app)
        return 'Game prepared. Subscribe to items in the VM’s Steam Workshop to download them.'


class WorkshopPanel(QWidget):
    def __init__(self, parent=None, network=None):
        super().__init__(parent)
        self.jobs = set()
        self.runtime = WorkshopVM(network)
        layout = QVBoxLayout(self)
        note = QLabel('Steam Workshop uses the Steam VM and its NordVPN connection. '
                      'Enter the game’s Steam App ID to prepare it with LuaMoon, then subscribe in Steam. '
                      'Added games remain in the VM until you remove them there.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.app = QLineEdit()
        self.app.setPlaceholderText('Steam App ID')
        layout.addWidget(self.app)
        self.item = QLineEdit()
        self.item.setPlaceholderText('Workshop item ID (optional)')
        layout.addWidget(self.item)
        self.status = QLabel('Ready')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = []
        for label, action in [('Prepare game and open Workshop', self.prepare),
                              ('Open Workshop item', self.open_item),
                              ('Open VM desktop', self.desktop), ('Open downloaded files', self.files), ('Refresh VM status', self.refresh)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()

    def task(self, function):
        from .plugin import Job
        if self.jobs:
            return
        job = Job(lambda: function(job.signals.progress.emit))
        self.jobs.add(job)
        for button in self.buttons:
            button.setEnabled(False)
        job.signals.progress.connect(self.status.setText)
        def finished(message):
            self.status.setText(message)
            for button in self.buttons:
                button.setEnabled(True)
            # Release after Qt finishes signal delivery.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.jobs.discard(job))
        job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(job)

    def closeEvent(self,event):
        if self.jobs:
            event.ignore();return
        super().closeEvent(event)

    def identifiers(self, require_item=False):
        source = self.app.text().strip()
        if not source.isascii() or not source.isdecimal() or not 0 < int(source) < 2**32:
            raise ValueError('Enter a valid Steam App ID.')
        item = self.item.text().strip()
        if (item or require_item) and (not item.isascii() or not item.isdecimal() or not 0 < int(item) < 2**64):
            raise ValueError('Enter a valid Workshop item ID.')
        return int(source), int(item) if item else None

    def prepare(self):
        try:
            app, item = self.identifiers()
        except ValueError as error:
            self.status.setText(str(error)); return
        def work(progress):
            message = self.runtime.prepare(app, progress)
            if item:
                self.runtime.request('workshop', app, item)
            return message
        self.task(work)

    def open_item(self):
        try:
            app, item = self.identifiers(True)
        except ValueError as error:
            self.status.setText(str(error)); return
        def work(progress):
            self.runtime.start()
            self.runtime.request('workshop', app, item)
            return 'Workshop item opened in the VM. Subscribe in Steam to download it.'
        self.task(work)

    def desktop(self):
        self.task(lambda progress:self.runtime.network.open_desktop())

    def files(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.runtime.network.cfg['shared'])/'Workshop')))

    def refresh(self):
        def work(progress):
            state = self.runtime.request('status')
            return ('Steam: ' + ('running' if state['steam_running'] else 'stopped') +
                    ' · LuaMoon: ' + ('installed' if state['moon_installed'] else 'missing') +
                    ' · VPN: ' + ('verified' if self.runtime.network.container else 'unavailable'))
        self.task(work)
