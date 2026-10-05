from PyQt6.QtCore import Qt, QCoreApplication, QObject, QRunnable, QThreadPool, QSettings, pyqtSignal
from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QComboBox, QPushButton, QLabel, QHBoxLayout, QGroupBox, QVBoxLayout
from playlite.providers import GenericPlugin
from .credentials import Wallet
from .network import Network
from .settings import Preferences

class Signals(QObject):
    finished = pyqtSignal(str)
    progress = pyqtSignal(str)

class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()
    def run(self):
        try:
            text = self.function()
        except Exception as error:
            text = str(error)
        self.signals.finished.emit(text)

class Plugin(GenericPlugin):
    def __init__(self):
        app = QCoreApplication.instance()
        self.network = getattr(app, '_playlite_depot_network', None) if app else None
        if self.network is None:
            self.network = Network()
            if app is not None:
                app._playlite_depot_network = self.network
        self.busy = False
        self.jobs = set()
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.shutdown)

    def shutdown(self):
        self.network.cancel()
        if not self.busy:
            try:
                self.network.disconnect()
            except RuntimeError:
                pass  # The independent guardian retries cleanup after process exit.

    def main_menu_actions(self, window):
        def open_downloader():
            from .download_dialog import DownloadDialog
            DownloadDialog(self.network, window).exec()
        return [('Steam Depot Downloader…', open_downloader)]

    def settings(self):
        return QSettings('Playlite', 'SteamDownloader')

    def create_settings(self, parent=None):
        widget = QWidget(parent)
        page = QVBoxLayout(widget)
        page.setContentsMargins(0, 8, 0, 0)
        page.setSpacing(16)
        note = QLabel('Downloads use an isolated VPN and a separate Steam session.')
        note.setWordWrap(True)
        page.addWidget(note)
        def section(title):
            box = QGroupBox(title)
            layout = QFormLayout(box)
            layout.setContentsMargins(16, 20, 16, 16)
            layout.setHorizontalSpacing(16)
            layout.setVerticalSpacing(12)
            layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            page.addWidget(box)
            return layout
        form = section('NordVPN')
        widget.country = QLineEdit(self.settings().value('country', ''))
        widget.country.setPlaceholderText('Automatic — NordVPN recommended server')
        form.addRow('Server country', widget.country)
        widget.protocol = QComboBox()
        widget.protocol.addItems(['udp', 'tcp'])
        widget.protocol.setCurrentText(self.settings().value('protocol', 'udp'))
        form.addRow('OpenVPN protocol', widget.protocol)
        widget.status = QLabel('Connection not checked')
        widget.status.setWordWrap(True)
        controls = QHBoxLayout()
        widget.buttons = []
        def save_credentials():
            from .authentication_dialog import CredentialDialog
            CredentialDialog('NordVPN', self.network, widget).exec()
        def connect():
            try:
                preferences = Preferences(widget.country.text().strip(), widget.protocol.currentText()).validate()
                credentials = Wallet().read()
            except Exception as error:
                widget.status.setText(str(error))
                return
            self.start(widget, lambda progress: self.network.connect(preferences, *credentials, progress=progress), cancellable=True)
        for text, callback in [('NordVPN login…', save_credentials), ('Connect', connect),
                               ('Check connection', lambda: self.start(widget, lambda progress: self.network.check())),
                               ('Disconnect', lambda: self.stop(widget))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
            button.setMinimumWidth(button.sizeHint().width())
            widget.buttons.append(button)
        controls.addStretch()
        form.addRow(controls)
        form.addRow(widget.status)
        widget.auth_status = {}
        form = section('Steam')
        def account_row(name):
            line = QHBoxLayout()
            status = QLabel('Status not checked')
            status.setWordWrap(True)
            widget.auth_status[name] = status
            button = QPushButton('Manage login…' if name != 'Hubcap' else 'Manage API key…')
            button.setFixedWidth(max(180, button.sizeHint().width()))
            def open_login():
                if name == 'Steam':
                    from .download_dialog import DownloadDialog
                    dialog = DownloadDialog(self.network, widget, authentication='steam')
                else:
                    from .authentication_dialog import CredentialDialog
                    dialog = CredentialDialog(name, self.network, widget)
                dialog.exec()
                refresh_status()
            button.clicked.connect(open_login)
            line.addWidget(status, 1); line.addWidget(button)
            if name == 'Steam':
                forget = QPushButton('Sign out')
                def sign_out():
                    from .credentials import SteamSessionWallet
                    account = self.settings().value('steam_account', '')
                    try:
                        if account: SteamSessionWallet(account).clear()
                        self.network.steam_confirmed = False
                        refresh_status()
                    except Exception:
                        status.setText('Could not remove saved login. Unlock KWallet and retry.')
                forget.clicked.connect(sign_out)
                line.addWidget(forget)

            if name != 'Steam':
                label = QLabel(name)
                label.setFixedWidth(90)
                line.insertWidget(0, label)
            form.addRow(line)
        account_row('Steam')
        form = section('Manifest providers')
        account_row('Moon')
        account_row('Hubcap')
        def refresh_status():
            from .credentials import MoonSessionWallet, SteamSessionWallet, HubcapKeyWallet
            account = self.settings().value('steam_account', '')
            stores = {'Moon': MoonSessionWallet(), 'Hubcap': HubcapKeyWallet(),
                      'Steam': SteamSessionWallet(account)}
            for name, store in stores.items():
                try:
                    saved = store.read() if name != 'Steam' or account else None
                    text = ('API key saved; verification pending' if name == 'Hubcap'
                            else 'Login saved' + (f' for {account}' if name == 'Steam' else '')) if saved else 'Not signed in'
                    widget.auth_status[name].setText(('Login confirmed this session' if name != 'Hubcap' else 'API key accepted this session') if saved and getattr(self.network, name.lower() + '_confirmed', False) else text)
                except Exception:
                    widget.auth_status[name].setText('Unlock KWallet to check saved login')
        check = QPushButton('Refresh login status')
        check.clicked.connect(refresh_status)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(check)
        page.addLayout(footer)
        refresh_status()
        return widget

    def stop(self, widget):
        if self.busy:
            self.network.cancel()
            widget.status.setText('Cancelling the VPN connection and removing temporary credentials…')
            widget.buttons[-1].setEnabled(False)
        else:
            self.start(widget, lambda progress: self.network.disconnect())

    def start(self, widget, function, cancellable=False):
        if self.busy:
            return
        self.busy = True
        if cancellable:
            self.network.cancelled.clear()
        for button in widget.buttons:
            button.setEnabled(False)
        widget.status.setText('Working on the isolated VPN connection…')
        if cancellable:
            widget.buttons[-1].setText('Cancel connection')
            widget.buttons[-1].setEnabled(True)
        job = Job(lambda: function(job.signals.progress.emit))
        def progress(text):
            try:
                if not self.network.cancelled.is_set():
                    widget.status.setText(text)
            except RuntimeError:
                pass
        job.signals.progress.connect(progress)
        self.jobs.add(job)
        def finished(text):
            self.busy = False
            self.jobs.discard(job)
            try:
                widget.status.setText(text)
                widget.buttons[-1].setText('Disconnect')
                for button in widget.buttons:
                    button.setEnabled(True)
            except RuntimeError:
                pass  # Settings may have been closed while the operation was running.
        job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(job)

    def save_settings(self, widget):
        preferences = Preferences(widget.country.text().strip(), widget.protocol.currentText()).validate()
        settings = self.settings()
        settings.setValue('country', preferences.country)
        settings.setValue('protocol', preferences.protocol)
        settings.sync()
