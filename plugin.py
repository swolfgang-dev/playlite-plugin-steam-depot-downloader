from pathlib import Path
import os
from PyQt6.QtCore import QTimer, Qt, QCoreApplication, QObject, QRunnable, QThreadPool, QSettings, pyqtSignal, pyqtSlot, QMetaObject, Q_ARG, QThread
from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QComboBox, QPushButton, QLabel, QHBoxLayout, QGroupBox, QVBoxLayout, QSizePolicy, QDialog, QFileDialog, QCheckBox
from playlite.providers import GenericPlugin
from .credentials import Wallet
from .network import Network
from .settings import Preferences
from .download_location import default_download_root

class Signals(QObject):
    finished = pyqtSignal(str)
    progress = pyqtSignal(str)

    @pyqtSlot(str)
    def deliver(self, text):
        self.finished.emit(text)

class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.setAutoDelete(False)
        self.function = function
        self.signals = Signals()
    def run(self):
        try:
            text = self.function()
        except Exception as error:
            text = str(error)
        try:
            if QThread.currentThread() == self.signals.thread():
                self.signals.deliver(text)
            else:
                QMetaObject.invokeMethod(self.signals, 'deliver', Qt.ConnectionType.QueuedConnection, Q_ARG(str, text))
        except RuntimeError:
            # QApplication can destroy signal owners while a shutdown job finishes.
            from PyQt6 import sip
            if not sip.isdeleted(self.signals):
                raise

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
            from .vm_backend import enabled, VMNetwork
            self.network = Network() if os.environ.get('PLAYLITE_STEAM_BACKEND')=='docker' else VMNetwork()
            if app is not None:
                app._playlite_depot_network = self.network
        self.busy = False
        self.jobs = set()
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.shutdown)

    def shutdown(self):
        self.network.cancel()
        if getattr(self.network,'is_vm',False) is True:
            try:self.network.close_session_background()
            except RuntimeError:pass
            return
        if not self.busy and not self.jobs:
            try:
                self.network.disconnect()
            except RuntimeError:
                pass  # The independent guardian retries cleanup after process exit.

    def before_launch(self,window,game):
        if getattr(self.network,'is_vm',False) is True:
            if self.busy or self.jobs:return True
            app=QCoreApplication.instance()
            if app and hasattr(app,'allWidgets') and any(getattr(widget,'network',None) is self.network and (getattr(widget,'busy',False) or getattr(widget,'jobs',None)) for widget in app.allWidgets()):return True
            from .vpn_lifecycle import has_downloads
            if has_downloads(self.network) or not (self.network.profile/'vm.json').exists():return True
            if self.settings().value('vm_ready_profile','')!=str(self.network.profile):return True
            if not hasattr(self,'session_games'):
                self.session_games=set()
                def changed(identity,state):
                    if state=='Stopped':
                        self.session_games.discard(identity)
                        if not self.session_games:self.network.resume()
                window.game_detection.changed.connect(changed)
            self.session_games.add(game['Id'])
            self.network.suspend()
            def failed_launch():
                if window.game_detection.status(game['Id'])=='Stopped':
                    self.session_games.discard(game['Id'])
                    if not self.session_games:self.network.resume()
            QTimer.singleShot(2000,failed_launch)
            return True
        from .vpn_lifecycle import disconnect_when_idle
        # Active jobs include setup and authentication; they must finish before
        # their shared network namespace can be released.
        if not self.busy and not self.jobs:
            disconnect_when_idle(self.network,release_session=True)
        return True

    def main_menu_actions(self, window):
        def open_downloader():
            from .download_dialog import DownloadDialog
            existing=next((dialog for dialog in window.findChildren(DownloadDialog) if not getattr(dialog,'queue_runner',False) and not dialog.authentication and dialog.isVisible()),None)
            dialog=existing or DownloadDialog(self.network,window)
            if existing and getattr(self.network,'is_vm',False) is True and self.settings().value('vm_ready_profile','')==str(self.network.profile):
                dialog.set_content_enabled(True)
                QTimer.singleShot(0,lambda:dialog.connect_vpn(automatic=True))
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.show();dialog.raise_();dialog.activateWindow()
        return [('Steam Downloader…', open_downloader)]

    def restore_downloads(self,window,queue):
        from .steam_queue_runner import SteamQueueRunner
        from .vpn_lifecycle import watch_queue
        watch_queue(self.network,queue)
        if getattr(self.network,'is_vm',False) is True and (self.network.profile/'vm.json').exists() and self.settings().value('vm_ready_profile','')==str(self.network.profile):
            from .settings import Preferences
            from .steam_runtime import start_with_vpn
            def start_session():
                settings=self.settings()
                self.network.cancelled.clear()
                self.network.connect(Preferences(settings.value('country',''),settings.value('protocol','tcp')))
                start_with_vpn(self.network)
                return 'Steam VM session ready.'
            job=Job(start_session);self.jobs.add(job)
            job.signals.finished.connect(lambda message:self.jobs.discard(job))
            QThreadPool.globalInstance().start(job)
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
        from .vm_backend import VMNetwork
        self.network=VMNetwork()
        app=QCoreApplication.instance()
        if app:app._playlite_depot_network=self.network
        from .steam_runtime_dialog import SteamRuntimeDialog
        if SteamRuntimeDialog(self.network,parent).exec()==QDialog.DialogCode.Accepted and parent is not None:
            owner=parent
            while owner.parentWidget() is not None:owner=owner.parentWidget()
            self.main_menu_actions(owner)[0][1]()

    def prepare_uninstall(self,parent=None):
        if getattr(self.network,'is_vm',False) is True:return True  # VM and shared games are retained.
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
        page.setSpacing(12)
        page.setAlignment(Qt.AlignmentFlag.AlignTop)
        vm=getattr(self.network,'is_vm',False) is True
        note = QLabel('Set up once, then choose games in Steam Downloader. Downloads go straight to your shared game folder.' if vm else 'Downloads use an isolated VPN and a separate Steam session.')
        note.setWordWrap(True)
        page.addWidget(note)
        def section(title):
            box = QGroupBox()
            layout = QFormLayout(box)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setHorizontalSpacing(16)
            layout.setVerticalSpacing(10)
            layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            heading = QLabel(f'<b>{title}</b>')
            layout.addRow(heading)
            page.addWidget(box)
            return layout
        form = section('NordVPN')
        vpn_box=form.parentWidget()
        widget.country = QLineEdit(self.settings().value('country', ''))
        widget.country.setPlaceholderText('Automatic — NordVPN recommended server')
        form.addRow('Server country', widget.country)
        widget.protocol = QComboBox()
        widget.protocol.addItems(['udp', 'tcp'])
        widget.protocol.setCurrentText(self.settings().value('protocol', 'tcp' if vm else 'udp'))
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
            if vm:
                self.network.open_desktop()
                self.start(widget,lambda progress:self.network.agent.rpc({'command':'open_nord'})['message'])
                return
            from .authentication_dialog import CredentialDialog
            CredentialDialog('NordVPN', self.network, widget).exec()
        def connect():
            try:
                preferences = Preferences(widget.country.text().strip(), widget.protocol.currentText()).validate()
                credentials = () if vm else Wallet().read()
            except Exception as error:
                widget.status.setText(str(error))
                return
            self.start(widget, lambda progress: self.network.connect(preferences, *credentials, progress=progress), cancellable=True)
        for text, callback in [('Sign in inside VM' if vm else 'Authenticate', save_credentials), ('Connect', connect),
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
        widget.keep_vpn_open=QCheckBox('Keep VM VPN connected while Playlite is open' if vm else 'Keep VPN connected for the Playlite session')
        widget.keep_vpn_open.setChecked(self.settings().value('keep_vpn_open',False,type=bool))
        form.addRow(widget.keep_vpn_open)
        if vm:
            widget.keep_vpn_open.setChecked(True);widget.keep_vpn_open.hide()
            # VM setup and session lifetime own the VPN connection. Keep the
            # internal preference widgets for settings compatibility only.
            widget.vpn_preferences_box=vpn_box
            page.removeWidget(vpn_box)
            vpn_box.hide()
        form = section('Steam environment' if vm else 'Isolated Steam')
        widget.keep_steam_open=QCheckBox('Keep Steam open for the Playlite session')
        widget.keep_steam_open.setChecked(self.settings().value('keep_steam_open',False,type=bool))
        if not vm:form.addRow(widget.keep_steam_open)
        if vm:widget.keep_steam_open.setChecked(True);widget.keep_steam_open.hide()
        lifetime=QLabel('Starts with Playlite, pauses while you play when no downloads are active, and shuts down when Playlite closes. Your games and sign-ins are kept.' if vm else 'Keeping Steam open also keeps its isolated VPN connected. Both stop when Playlite exits. Disconnect stops both immediately.')
        lifetime.setWordWrap(True);form.addRow(lifetime)
        widget.auto_start=QCheckBox('Connect VPN and start Steam when opening the downloader')
        widget.auto_start.setChecked(self.settings().value('auto_start_downloader',True,type=bool))
        if not vm:form.addRow(widget.auto_start)
        if vm:widget.auto_start.setChecked(True);widget.auto_start.hide()
        widget.steam_status=QLabel('Steam: checking status…')
        widget.steam_status.setWordWrap(True)
        widget.steam_status.setMinimumHeight(widget.steam_status.fontMetrics().lineSpacing())
        form.addRow(widget.steam_status)
        widget.steam_busy=False
        widget.steam_checking=False
        widget.steam_generation=0
        widget.steam_state='unknown'
        widget.steam_error=''
        widget.steam_action=QPushButton('Set up Steam…')
        widget.steam_setup_button=QPushButton('Open setup…')
        if vm:widget.steam_setup_button.setText('Setup and sign-in…')
        else:widget.steam_setup_button.hide()
        widget.stop_steam_button=QPushButton('Stop Steam environment')
        widget.delete_steam_button=QPushButton('Delete isolated Steam…')
        if vm:widget.delete_steam_button.setText('Delete VM…')
        widget.steam_hint=QLabel('')
        widget.steam_hint.setWordWrap(True)
        def open_setup(install=False):
            if self.busy or widget.steam_busy:
                widget.steam_status.setText('Steam: wait for the current operation to finish.');return
            from .vpn_lifecycle import has_downloads
            if install and has_downloads(self.network):
                widget.steam_status.setText('Steam: pause or cancel downloads before setup.');return
            self.save_settings(widget)
            from .steam_runtime_dialog import SteamRuntimeDialog
            widget.steam_busy=True
            self.update_steam_controls(widget)
            try:
                dialog=SteamRuntimeDialog(self.network,widget)
                if install:QTimer.singleShot(0,dialog.install)
                elif not vm:QTimer.singleShot(0,dialog.desktop)
                accepted=dialog.exec()==QDialog.DialogCode.Accepted
                if vm and accepted:
                    owner=widget
                    while owner.parentWidget() is not None:owner=owner.parentWidget()
                    self.main_menu_actions(owner)[0][1]()
            finally:
                widget.steam_busy=False
                self.refresh_steam(widget)
        widget.open_steam_setup=open_setup
        def steam_action():
            if widget.steam_state in ('missing','incomplete','unknown'):open_setup(True)
            elif widget.steam_state=='running':
                if vm:self.steam_task(widget,lambda progress:self.network.open_desktop() or '',lambda _:None)
                else:open_setup(False)
            else:self.start_steam(widget)
        widget.steam_action.clicked.connect(steam_action)
        if vm:
            authentication=QPushButton('Saved credentials…')
            def edit_authentication():
                from .vm_authentication_dialog import VMAuthenticationDialog
                VMAuthenticationDialog(self.network,widget).exec()
            authentication.clicked.connect(edit_authentication)
            authentication.setToolTip('Update your NordVPN token and provider credentials. Use Setup and sign-in to complete Steam authentication.')
        widget.steam_setup_button.clicked.connect(lambda:open_setup(not vm))
        widget.stop_steam_button.clicked.connect(lambda:self.stop_steam(widget))
        widget.delete_steam_button.clicked.connect(lambda:self.delete_steam(widget))
        line=QHBoxLayout()
        line.setSpacing(10)
        if vm:
            line.addWidget(widget.steam_setup_button)
            line.addWidget(widget.steam_action)
        else:
            for button in (widget.steam_action,widget.steam_setup_button,widget.stop_steam_button,widget.delete_steam_button):line.addWidget(button)
        form.addRow(line)
        if vm:
            maintenance=QWidget()
            maintenance_layout=QVBoxLayout(maintenance)
            maintenance_layout.setContentsMargins(0,0,0,0)
            maintenance_layout.setSpacing(10)
            maintenance_toggle=QCheckBox('Maintenance')
            maintenance_layout.addWidget(maintenance_toggle)
            maintenance_body=QWidget(maintenance)
            maintenance_buttons=QHBoxLayout(maintenance_body)
            maintenance_buttons.setSpacing(10)
            maintenance_buttons.setContentsMargins(0,0,0,0)
            for button in (authentication,widget.stop_steam_button,widget.delete_steam_button):maintenance_buttons.addWidget(button)
            maintenance_layout.addWidget(maintenance_body)
            maintenance_body.hide()
            maintenance_toggle.toggled.connect(maintenance_body.setVisible)
            form.addRow(maintenance)
        form.addRow(widget.steam_hint)
        if vm:
            widget.steam_hint.setVisible(False)
        manage=QPushButton('Manage installed content…')
        def manage_content():
            if self.busy or widget.steam_busy:return
            from .installed_games_dialog import InstalledGamesDialog
            InstalledGamesDialog(self.network,widget).exec()
        manage.clicked.connect(manage_content)
        manage_row=QHBoxLayout()
        manage_row.addWidget(manage)
        manage_row.addStretch()
        if not vm:form.addRow(manage_row)
        widget.steam_timer=QTimer(widget)
        widget.steam_timer.setInterval(5000)
        widget.steam_timer.timeout.connect(lambda:self.refresh_steam(widget) if widget.isVisible() else None)
        widget.steam_timer.start()
        form = section('Game files' if vm else 'Downloads')
        widget.download_root = QLineEdit(default_download_root() if vm else self.settings().value('download_root', '', type=str))
        from .download_location import general_download_root
        widget.download_root.setPlaceholderText(general_download_root() + ' (automatic)')
        browse_root = QPushButton('Browse…')
        def choose_root():
            folder = QFileDialog.getExistingDirectory(widget, 'Default download location', widget.download_root.text() or default_download_root())
            if folder: widget.download_root.setText(folder)
        browse_root.clicked.connect(choose_root)
        root_line = QHBoxLayout(); root_line.setSpacing(10)
        root_line.addWidget(widget.download_root); root_line.addWidget(browse_root)
        form.addRow('Shared download folder' if vm else 'Default location', root_line)
        hint = QLabel('Steam installs and verifies games here. Playlite uses the same files, with no copy step. Leave empty to use Playlite’s default installation folder. Changing this folder restarts the VM; existing games stay in their original folder.' if vm else 'Leave empty to use the General installation folder automatically. Each game downloads into its own folder; missing folders are created automatically.')
        hint.setWordWrap(True); form.addRow(hint)
        if vm:
            manage.setText('Manage downloaded games…')
            form.addRow(manage_row)
        def on_open():
            widget.settings_closed=False
            if not widget.isVisible():return
            self.refresh_steam(widget)
            if not self.busy:
                if self.network.container:self.start(widget,lambda progress:self.network.check())
                else:widget.status.setText('VPN disconnected')
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
        self.steam_task(widget,lambda progress:SteamRuntime(self.network).stop(),lambda message:self.refresh_steam(widget))

    def update_steam_controls(self,widget):
        from .vpn_lifecycle import has_downloads
        active=has_downloads(self.network)
        busy=self.busy or widget.steam_busy
        widget.steam_action.setEnabled(not busy and not (active and widget.steam_state in ('missing','incomplete','unknown')))
        widget.steam_setup_button.setVisible(getattr(self.network,'is_vm',False) is True or widget.steam_state=='error')
        widget.steam_setup_button.setEnabled(not busy and not active)
        widget.stop_steam_button.setEnabled(not busy and not active and widget.steam_state=='running')
        widget.delete_steam_button.setEnabled(not busy and not active)
        widget.steam_hint.setVisible(active or busy or getattr(self.network,'is_vm',False) is not True)
        widget.steam_hint.setText('Pause or cancel active and queued downloads before setup or stopping Steam.' if active else 'Wait for the current operation to finish.' if busy else '' if getattr(self.network,'is_vm',False) is True else 'Exported games and NordVPN credentials are kept when deleting Steam.')

    def steam_task(self,widget,operation,done,inspection=False):
        if self.busy or widget.steam_busy:return
        if inspection:
            if widget.steam_checking:return
            widget.steam_checking=True
        else:
            widget.steam_generation+=1
            widget.steam_busy=True
            widget.steam_error=''
            self.update_steam_controls(widget)
        generation=widget.steam_generation
        result=[]
        def work():
            result.append(operation(job.signals.progress.emit))
            return ''
        job=Job(work);self.jobs.add(job)
        job.signals.progress.connect(widget.steam_status.setText)
        def finished(error):
            if inspection:widget.steam_checking=False
            else:widget.steam_busy=False
            self.jobs.discard(job)
            if widget.settings_closed:return
            if inspection and (widget.steam_busy or generation!=widget.steam_generation):return
            if result:done(result[0])
            else:
                widget.steam_state='error'
                widget.steam_error=error
                widget.steam_status.setText('Steam: '+error)
                widget.steam_action.setText('Retry Steam')
            self.update_steam_controls(widget)
        job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(job)

    def refresh_steam(self,widget):
        from .steam_runtime import installation_status
        def render(state):
            if widget.steam_error:
                widget.steam_state='error'
                widget.steam_status.setText('Steam: '+widget.steam_error)
                widget.steam_action.setText('Retry Steam')
                return
            widget.steam_state=state
            labels={'missing':('not installed','Set up Steam…'),'incomplete':('setup incomplete','Set up Steam…'),'stopped':('stopped','Start Steam'),'running':('running','Open desktop…')}
            text,action=labels[state]
            widget.steam_status.setText('Steam: '+text)
            widget.steam_action.setText(action)
        self.steam_task(widget,lambda progress:installation_status(self.network),render,inspection=True)

    def start_steam(self,widget):
        if not self.network.container:
            widget.steam_status.setText('Steam: connect the VPN before starting.');return
        from .steam_runtime import start_with_vpn
        self.steam_task(widget,lambda progress:start_with_vpn(self.network,progress),lambda message:(widget.steam_status.setText(message),self.refresh_steam(widget)))

    def delete_steam(self,widget):
        if getattr(self.network,'is_vm',False) is True:
            from .vpn_lifecycle import has_downloads
            from PyQt6.QtWidgets import QMessageBox
            if self.busy or self.jobs or widget.steam_busy or has_downloads(self.network):
                widget.steam_status.setText('Finish setup and pause or cancel downloads before deleting the VM.');return
            if not (self.network.profile/'vm.json').exists():
                widget.steam_status.setText('No Steam VM is installed.');return
            answer=QMessageBox.question(widget,'Delete Steam VM?',
                'Delete the Steam VM and its private disk, including Steam, NordVPN and provider logins?\n\nShared game and Workshop files will be kept. You can create a new VM with VM setup.',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
            if answer!=QMessageBox.StandardButton.Yes:return
            def removed(message):
                self.settings().remove('vm_ready_profile')
                widget.steam_status.setText(message);widget.steam_state='missing';self.update_steam_controls(widget)
            self.steam_task(widget,lambda progress:self.network.delete_vm(progress),removed)
            return
        from .vpn_lifecycle import has_downloads
        if self.busy or widget.steam_busy or has_downloads(self.network):
            widget.steam_status.setText('Steam: finish setup or pause/cancel downloads before deleting.');return
        from .environment_uninstall import EnvironmentRemovalDialog
        widget.steam_busy=True
        self.update_steam_controls(widget)
        try:EnvironmentRemovalDialog(self.network,widget,environment_only=True).exec()
        finally:
            widget.steam_busy=False
            if not self.network.container:widget.status.setText('VPN disconnected')
            self.refresh_steam(widget)

    def start(self, widget, function, cancellable=False,start_steam=False):
        if self.busy:
            return
        self.busy = True
        if isinstance(widget,SettingsWidget):self.update_steam_controls(widget)
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
                if isinstance(widget,SettingsWidget):
                    self.update_steam_controls(widget)
                    self.refresh_steam(widget)
            except RuntimeError:
                pass  # Settings may have been closed while the operation was running.
        job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(job)

    def save_settings(self, widget):
        preferences = Preferences(widget.country.text().strip(), widget.protocol.currentText()).validate()
        settings = self.settings()
        value = widget.download_root.text().strip()
        root = Path(value).expanduser() if value else None
        if root is not None and not root.is_absolute():raise ValueError('Choose an absolute default download location.')
        if getattr(self.network,'is_vm',False) is True and (self.network.profile/'vm.json').exists():
            if root is None:
                from .download_location import general_download_root
                root=Path(general_download_root())
            self.network.set_shared_folder(root)
        settings.setValue('download_root', str(root) if value and root is not None else '')
        settings.setValue('country', preferences.country)
        settings.setValue('protocol', preferences.protocol)
        settings.setValue('keep_steam_open',widget.keep_steam_open.isChecked())
        settings.setValue('keep_vpn_open',widget.keep_vpn_open.isChecked())
        settings.setValue('auto_start_downloader',widget.auto_start.isChecked())
        settings.sync()
