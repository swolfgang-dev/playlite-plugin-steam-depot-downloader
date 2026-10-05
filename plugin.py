from PyQt6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, QSettings, pyqtSignal
from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QComboBox, QPushButton, QLabel, QHBoxLayout
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
        form = QFormLayout(widget)
        note = QLabel('Uses a dedicated Docker/OpenVPN container; host Steam is not accessed. The depot downloader is an experimental, explicit test workflow.')
        note.setWordWrap(True)
        form.addRow(note)
        widget.country = QLineEdit(self.settings().value('country', ''))
        widget.country.setPlaceholderText('Automatic — any supported country')
        form.addRow('Server country', widget.country)
        widget.protocol = QComboBox()
        widget.protocol.addItems(['udp', 'tcp'])
        widget.protocol.setCurrentText(self.settings().value('protocol', 'udp'))
        form.addRow('OpenVPN protocol', widget.protocol)
        widget.username = QLineEdit()
        widget.password = QLineEdit()
        widget.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('NordVPN service username', widget.username)
        form.addRow('NordVPN service password', widget.password)
        info = QLabel('<a href="https://support.nordvpn.com/hc/en-us/articles/19685514639633">Find service credentials in Nord Account</a>. Your regular account password is not used. Saved credentials use KWallet.')
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        form.addRow(info)
        widget.status = QLabel('Disconnected. Downloads disabled.')
        widget.status.setWordWrap(True)
        controls = QHBoxLayout()
        widget.buttons = []
        def save_credentials():
            try:
                Wallet().save(widget.username.text(), widget.password.text())
                widget.password.clear()
                widget.username.clear()
                widget.status.setText('Service credentials saved in KWallet.')
            except Exception as error:
                widget.status.setText(str(error))
        def connect():
            try:
                preferences = Preferences(widget.country.text().strip(), widget.protocol.currentText()).validate()
                credentials = Wallet().read()
            except Exception as error:
                widget.status.setText(str(error))
                return
            self.start(widget, lambda progress: self.network.connect(preferences, *credentials, progress=progress), cancellable=True)
        for text, callback in [('Save credentials', save_credentials), ('Connect', connect),
                               ('Check connection', lambda: self.start(widget, lambda progress: self.network.check())),
                               ('Disconnect', lambda: self.stop(widget))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
            widget.buttons.append(button)
        form.addRow(controls)
        form.addRow(widget.status)
        download = QPushButton("Manage authentication…")
        def open_downloader():
            from .download_dialog import DownloadDialog
            dialog = DownloadDialog(self.network, widget, authentication=True)
            dialog.exec()
        download.clicked.connect(open_downloader)
        form.addRow(download)
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
