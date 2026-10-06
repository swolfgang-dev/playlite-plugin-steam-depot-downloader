from pathlib import Path
from PyQt6.QtCore import QTimer, Qt, QCoreApplication, QObject, QRunnable, QThreadPool, QSettings, pyqtSignal
from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QComboBox, QPushButton, QLabel, QHBoxLayout, QGroupBox, QVBoxLayout, QSizePolicy, QDialog, QFileDialog, QCheckBox
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

class SettingsWidget(QWidget):
    """Start the connection only when the settings page becomes visible."""
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.on_open)

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
            existing=next((dialog for dialog in window.findChildren(DownloadDialog) if not getattr(dialog,'queue_runner',False) and not dialog.authentication and dialog.isVisible()),None)
            dialog=existing or DownloadDialog(self.network,window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.show();dialog.raise_();dialog.activateWindow()
        return [('Steam Downloader…', open_downloader)]

    def restore_downloads(self,window,queue):
        from .steam_queue_runner import SteamQueueRunner
        from .vpn_lifecycle import watch_queue
        watch_queue(self.network,queue)
        for row in queue.entries:
            if row.metadata.get('backend')!='isolated_steam':continue
            snapshot=row.metadata.get('snapshot',{})
            if (type(snapshot.get('app')) is not int or not 0<snapshot['app']<2**32
                    or snapshot.get('platform') not in ('windows','linux')):continue
            row.steam_appid=snapshot['app']
            row.factory=lambda queue,entry,snapshot=snapshot:SteamQueueRunner(self.network,window,queue,entry,snapshot)
            if row.state=='Complete':row.controller=row.factory(queue,row)

    def settings(self):
        return QSettings('Playlite', 'SteamDownloader')

    def post_install(self,parent=None):
        from .steam_runtime_dialog import SteamRuntimeDialog
        SteamRuntimeDialog(self.network,parent).exec()

    def prepare_uninstall(self,parent=None):
        from .environment_uninstall import EnvironmentRemovalDialog
        return EnvironmentRemovalDialog(self.network,parent).exec()==QDialog.DialogCode.Accepted

    def create_settings(self, parent=None):
        widget = SettingsWidget(parent)
        widget.network = self.network
        widget.settings_closed = False
        owner = widget.window()
        if isinstance(owner, QDialog):
            owner.finished.connect(lambda *_: self.close_settings(widget))
        page = QVBoxLayout(widget)
        page.setContentsMargins(0, 8, 0, 0)
        page.setSpacing(16)
        page.setAlignment(Qt.AlignmentFlag.AlignTop)
        note = QLabel('Downloads use an isolated VPN and a separate Steam session.')
        note.setWordWrap(True)
        page.addWidget(note)
        def section(title):
            box = QGroupBox()
            layout = QFormLayout(box)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setHorizontalSpacing(16)
            layout.setVerticalSpacing(12)
            layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            heading = QLabel(f'<b>{title}</b>')
            layout.addRow(heading)
            page.addWidget(box)
            return layout
        form = section('NordVPN')
        widget.country = QLineEdit(self.settings().value('country', ''))
        widget.country.setPlaceholderText('Automatic — NordVPN recommended server')
        form.addRow('Server country', widget.country)
        widget.protocol = QComboBox()
        widget.protocol.addItems(['udp', 'tcp'])
        widget.protocol.setCurrentText(self.settings().value('protocol', 'udp'))
        widget.protocol.setFixedWidth(widget.protocol.sizeHint().width())
        form.addRow('OpenVPN protocol', widget.protocol)
        widget.status = QLabel('Connection not checked')
        widget.status.setWordWrap(True)
        widget.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        widget.status.setMinimumHeight(widget.status.fontMetrics().height() * 2 + 8)
        controls = QHBoxLayout()
        controls.setSpacing(10)
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
        for text, callback in [('Authenticate', save_credentials), ('Connect', connect),
                               ('Check connection', lambda: self.start(widget, lambda progress: self.network.check())),
                               ('Disconnect', lambda: self.stop(widget))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            if not widget.buttons:
                account_controls = QHBoxLayout()
                account_controls.setSpacing(10)
                account_controls.addWidget(button)
                account_controls.addStretch()
                form.insertRow(1, 'Service account', account_controls)
            else:
                controls.addWidget(button)
            button.setMinimumWidth(button.sizeHint().width())
            widget.buttons.append(button)
        controls.addStretch()
        form.addRow('Connection', controls)
        form.addRow(widget.status)
        widget.keep_vpn_open=QCheckBox('Keep VPN connected for the Playlite session')
        widget.keep_vpn_open.setChecked(self.settings().value('keep_vpn_open',False,type=bool))
        form.addRow(widget.keep_vpn_open)
        form = section('Isolated Steam')
        widget.keep_steam_open=QCheckBox('Keep Steam open for the Playlite session')
        widget.keep_steam_open.setChecked(self.settings().value('keep_steam_open',False,type=bool))
        form.addRow(widget.keep_steam_open)
        lifetime=QLabel('Keeping Steam open also keeps its isolated VPN connected. Both stop when Playlite exits. Disconnect stops both immediately.')
        lifetime.setWordWrap(True);form.addRow(lifetime)
        setup = QPushButton('Set up isolated Steam…')
        desktop = QPushButton('Open Steam desktop…')
        widget.stop_steam_button=QPushButton('Stop Steam environment')
        widget.stop_steam_button.clicked.connect(lambda:self.stop_steam(widget))
        manage = QPushButton('Manage installed content…')
        explanation = QLabel('Set up Steam and LuaMoon in a private container. Steam and manifest provider authentication are managed in its desktop. Saved container sessions are retained.')
        explanation.setWordWrap(True)
        form.addRow(explanation)
        def open_setup(install=False):
            if self.busy:
                widget.status.setText('Wait for the VPN connection to finish before opening setup.')
                return
            self.save_settings(widget)
            from .steam_runtime_dialog import SteamRuntimeDialog
            dialog = SteamRuntimeDialog(self.network, widget)
            if install: QTimer.singleShot(0, dialog.install)
            else: QTimer.singleShot(0, dialog.desktop)
            dialog.exec()
        setup.clicked.connect(lambda: open_setup(True))
        desktop.clicked.connect(lambda: open_setup(False))
        line = QHBoxLayout(); line.setSpacing(10)
        def manage_content():
            if self.busy:
                widget.status.setText('Wait for the VPN connection to finish.');return
            from .installed_games_dialog import InstalledGamesDialog
            InstalledGamesDialog(self.network,widget).exec()
        manage.clicked.connect(manage_content)
        line.addWidget(setup); line.addWidget(desktop);line.addWidget(widget.stop_steam_button); line.addStretch()
        form.addRow(line)
        form.addRow(manage)
        form = section('Downloads')
        widget.download_root = QLineEdit(self.settings().value('download_root', str(Path.home() / 'Downloads')))
        browse_root = QPushButton('Browse…')
        def choose_root():
            folder = QFileDialog.getExistingDirectory(widget, 'Default download location', widget.download_root.text())
            if folder: widget.download_root.setText(folder)
        browse_root.clicked.connect(choose_root)
        root_line = QHBoxLayout(); root_line.setSpacing(10)
        root_line.addWidget(widget.download_root); root_line.addWidget(browse_root)
        form.addRow('Default location', root_line)
        hint = QLabel('Each game downloads into its own folder here. Missing folders are created automatically.')
        hint.setWordWrap(True); form.addRow(hint)
        def on_open():
            widget.settings_closed = False
            if self.busy or not widget.isVisible(): return
            if self.network.container:
                self.start(widget, lambda progress: self.network.check())
                return
            try:
                credentials = Wallet().read()
                preferences = Preferences(widget.country.text().strip(), widget.protocol.currentText()).validate()
            except ValueError:
                return  # No saved service credentials; wait for an explicit login.
            except Exception as error:
                widget.status.setText(str(error))
                return
            self.start(widget, lambda progress: self.network.connect(preferences, *credentials, progress=progress), cancellable=True)
        widget.on_open = on_open
        return widget

    def close_settings(self, widget):
        widget.settings_closed = True
        from .vpn_lifecycle import has_downloads,disconnect_when_idle
        if has_downloads(self.network):
            disconnect_when_idle(self.network)
            return
        if not self.busy:self.disconnect_after_settings()

    def disconnect_after_settings(self):
        from .vpn_lifecycle import disconnect_when_idle
        disconnect_when_idle(self.network)

    def stop(self, widget):
        if self.busy:
            self.network.cancel()
            widget.status.setText('Cancelling the VPN connection and removing temporary credentials…')
            widget.buttons[-1].setEnabled(False)
        else:
            self.start(widget, lambda progress: self.network.disconnect())

    def stop_steam(self,widget):
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.network):
            widget.status.setText('Pause or cancel queued and active downloads before stopping Steam.');return
        if self.busy:
            widget.status.setText('Wait for the current operation to finish before stopping Steam.');return
        from .steam_runtime import SteamRuntime
        self.start(widget,lambda progress:SteamRuntime(self.network).stop(),start_steam=False)

    def start(self, widget, function, cancellable=False,start_steam=True):
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
        def operation():
            result=function(job.signals.progress.emit)
            if start_steam and self.network.container:
                from .steam_runtime import start_with_vpn
                start_with_vpn(self.network,job.signals.progress.emit)
            return result
        job = Job(operation)
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
            if widget.settings_closed:
                self.disconnect_after_settings()
                return
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
        root=Path(widget.download_root.text().strip() or str(Path.home()/'Downloads')).expanduser()
        if not root.is_absolute():raise ValueError('Choose an absolute default download location.')
        settings.setValue('download_root',str(root))
        settings.setValue('country', preferences.country)
        settings.setValue('protocol', preferences.protocol)
        settings.setValue('keep_steam_open',widget.keep_steam_open.isChecked())
        settings.setValue('keep_vpn_open',widget.keep_vpn_open.isChecked())
        settings.sync()
