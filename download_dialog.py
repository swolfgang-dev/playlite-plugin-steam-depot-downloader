"""Explicit manifest and Steam login workflow; never uses a host Steam profile."""
import tempfile
import base64
import uuid
import time
import re
from pathlib import Path
from PyQt6.QtCore import QProcess, QThreadPool, QSettings, QTimer, Qt, QSize, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QDesktopServices
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QPushButton,
                            QLabel, QPlainTextEdit, QFileDialog, QHBoxLayout, QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QProgressBar, QSizePolicy, QMenu)
from .moon import Moon
from .credentials import MoonSessionWallet, SteamSessionWallet, HubcapKeyWallet
from .providers import SOURCES, Transport, prepare_depot
from .worker import Request, worker_args, require_download_success

class DownloadDialog(QDialog):
    def __init__(self, network, parent=None, authentication=False):
        super().__init__(parent)
        self.authentication = authentication
        self.network = network
        self.transport = Transport(network)
        self.moon = Moon(self.transport, MoonSessionWallet())
        self.rows = []
        self.content_info = None
        self.content_plan = []
        self.content_index = 0
        self.game_name = ''
        self.selected_app = None
        self.owns_connection = False
        self.jobs = set()
        self.process = None
        self.temporary = None
        self.container = None
        self.output = ''
        self.total_downloaded = 0
        self.total_uncompressed = 0
        self.batch_started_at = 0
        self.setWindowTitle('Steam Depot Downloader — Playlite')
        self.resize(850, 720)
        form = QFormLayout(self)
        self.main_form = form
        form.setHorizontalSpacing(14); form.setVerticalSpacing(12)
        auth_rows = []; download_rows = []
        notice = QLabel('Downloads use a separate Steam session inside the VPN. Use an account that owns the game. Moon login is saved in KWallet and restored automatically. Select optional DLC; the destination must be empty.')
        notice.setWordWrap(True)
        form.addRow(notice)
        self.appid = QLineEdit()
        self.appid.setPlaceholderText('Steam App ID or game name')
        search_button = QPushButton('Search')
        search_button.clicked.connect(self.open_search)
        search_line = QHBoxLayout(); search_line.setSpacing(10)
        search_line.addWidget(self.appid); search_line.addWidget(search_button)
        form.addRow('Game / App ID', search_line); download_rows.append(search_line)
        self.cover_placeholder=QPixmap(54,81)
        self.cover_placeholder.fill(Qt.GlobalColor.transparent)
        self.search_results = QListWidget()
        self.search_results.setIconSize(QSize(54,81))
        self.search_results.setMaximumHeight(230)
        self.search_results.itemClicked.connect(self.select_game)
        self.search_results.itemActivated.connect(self.select_game)
        form.addRow(self.search_results); download_rows.append(self.search_results)
        self.search_results.hide()
        from .game_search import GameSearch
        self.game_search = GameSearch(self.transport)
        self.search_timer = QTimer(self); self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(650)
        self.search_timer.timeout.connect(self.search_games)
        self.appid.returnPressed.connect(self.open_search)
        self.search_generation = 0
        self.searching = False
        self.search_items = {}

        self.provider = QComboBox(); self.provider.addItems(SOURCES)
        self.provider.setCurrentText(QSettings('Playlite','SteamDownloader').value('manifest_provider','Hubcap'))
        form.addRow('Manifest provider', self.provider); download_rows.append(self.provider)
        self.code = QLineEdit(); self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setPlaceholderText('Run /login in the LuaTools Discord; paste the six-character code')
        login = QPushButton('Authenticate'); login.clicked.connect(self.login)
        line = QHBoxLayout(); line.setSpacing(10); line.addWidget(self.code); line.addWidget(login)
        logout = QPushButton('Sign out'); logout.clicked.connect(lambda: self.task(self.moon.logout, self.status.setText))
        line.addWidget(logout)
        form.addRow('Moon login code', line); auth_rows.append(line)
        discord = QLabel('<a href="https://discord.gg/luatools">Open LuaTools Discord</a> — run /login to get a six-character code.')
        discord.setOpenExternalLinks(True); discord.setWordWrap(True)
        form.addRow(discord); auth_rows.append(discord)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        save_key = QPushButton('Save API key'); save_key.clicked.connect(self.save_key)
        line = QHBoxLayout(); line.setSpacing(10); line.addWidget(self.key); line.addWidget(save_key)
        form.addRow('Hubcap API key', line); auth_rows.append(line)
        self.fetch = QPushButton('Fetch manifest pack'); self.fetch.clicked.connect(self.fetch_pack)
        form.addRow(self.fetch); download_rows.append(self.fetch)
        self.depot = QComboBox(); form.addRow('Depot / manifest', self.depot); download_rows.append(self.depot)
        self.username = QLineEdit(QSettings("Playlite", "SteamDownloader").value("steam_account", ""))
        forget = QPushButton('Forget Steam login')
        forget.clicked.connect(self.forget_steam)
        line = QHBoxLayout(); line.setSpacing(10); line.addWidget(self.username); line.addWidget(forget)
        form.addRow('Steam account name', line); auth_rows.append(line)
        self.destination = QLineEdit()
        self.default_root = QSettings('Playlite','SteamDownloader').value('download_root', str(Path.home()/'Downloads'))
        browse = QPushButton('Browse…'); browse.clicked.connect(self.browse)
        line = QHBoxLayout(); line.setSpacing(10); line.addWidget(self.destination); line.addWidget(browse)
        form.addRow('Download folder', line); download_rows.append(line)
        self.start_button = QPushButton('Download selected depot'); self.start_button.clicked.connect(self.prepare_download)
        form.addRow(self.start_button)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); form.addRow(self.log)
        self.response = QLineEdit(); self.response.setEchoMode(QLineEdit.EchoMode.Password)
        self.response.setPlaceholderText('Enter Steam password or Steam Guard code when requested in the log')
        self.send = QPushButton('Send securely'); self.send.clicked.connect(self.send_response)
        self.response.returnPressed.connect(self.send_response)
        line = QHBoxLayout(); line.setSpacing(10); line.addWidget(self.response); line.addWidget(self.send)
        form.addRow('Steam login response', line); auth_rows.append(line)
        self.status = QLabel('Connect the VPN and build the native worker image before continuing.')
        self.status.setWordWrap(True); form.addRow(self.status)
        self.cancel = QPushButton('Close'); self.cancel.clicked.connect(self.close_or_cancel)
        form.addRow(self.cancel)
        self.busy = False
        for row in (download_rows if authentication else auth_rows):
            form.setRowVisible(row, False)
        if authentication:
            self.setWindowTitle('Steam Depot Downloader authentication — Playlite')
            self.start_button.setText('Authenticate')
            notice.setText('Manage Moon, Hubcap and Steam authentication here. Login sessions are saved in KWallet. Connect the VPN in plugin settings first.')
            if authentication == 'steam':
                for index in range(form.rowCount()):
                    form.setRowVisible(index, False)
                self.setWindowTitle('Steam login — Playlite')
                self.resize(560, 300)
                self.pending_password = ''
                self.login_succeeded = False
                self.steam_form = QFormLayout()
                self.steam_form.setVerticalSpacing(12)
                notice = QLabel('Sign in to your Steam download account.')
                self.steam_form.addRow(notice)
                self.username.setParent(self)
                self.steam_form.addRow('Account name', self.username)
                self.password = QLineEdit()
                self.password.setEchoMode(QLineEdit.EchoMode.Password)
                self.password.returnPressed.connect(self.download)
                self.steam_form.addRow('Password', self.password)
                self.response.setParent(self)
                self.send.setParent(self)
                self.send.setText('Continue')
                self.guard_row = QHBoxLayout()
                self.guard_row.addWidget(self.response); self.guard_row.addWidget(self.send)
                self.response.setPlaceholderText('Steam Guard code')
                self.steam_form.addRow('Steam Guard', self.guard_row)
                self.steam_form.setRowVisible(self.guard_row, False)
                self.status.setParent(self)
                self.status.setText('Your login will be saved securely in KWallet.')
                self.steam_form.addRow(self.status)
                self.vpn_button = QPushButton('Connect VPN')
                self.vpn_button.clicked.connect(self.connect_vpn)
                self.steam_form.addRow(self.vpn_button)
                self.steam_form.setRowVisible(self.vpn_button, False)
                details = QPushButton('Show details')
                details.setCheckable(True)
                self.log.setParent(self); self.log.setMaximumHeight(150)
                self.steam_form.addRow(self.log)
                self.steam_form.setRowVisible(self.log, False)
                def toggle_details(visible):
                    self.steam_form.setRowVisible(self.log, visible)
                    details.setText('Hide details' if visible else 'Show details')
                    self.adjustSize()
                details.toggled.connect(toggle_details)
                self.start_button.setParent(self); self.start_button.setText('Authenticate')
                self.cancel.setParent(self)
                footer = QHBoxLayout(); footer.setSpacing(10)
                footer.addWidget(details); footer.addStretch()
                footer.addWidget(self.start_button); footer.addWidget(self.cancel)
                self.steam_form.addRow(footer)
                form.addRow(self.steam_form)
                for control in (self.username, self.status, self.start_button, self.cancel):
                    control.show()
        else:
            notice.setText('Choose a game, then download its files.')
            self.resize(780, 600)
            self.vpn_status = QLabel('Checking VPN connection…')
            self.vpn_status.setWordWrap(True)
            self.vpn_connect = QPushButton('Connect VPN')
            self.vpn_connect.clicked.connect(self.connect_vpn)
            vpn_line = QHBoxLayout(); vpn_line.setSpacing(10)
            vpn_line.addWidget(self.vpn_status,1); vpn_line.addWidget(self.vpn_connect)
            form.insertRow(1,vpn_line)
            self.game_cover = QLabel(); self.game_cover.setFixedSize(72,108)
            self.game_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.game_title = QLabel('Search for a game or enter its Steam App ID.')
            self.game_title.setWordWrap(True)
            card = QHBoxLayout(); card.setSpacing(14)
            card.addWidget(self.game_cover); card.addWidget(self.game_title,1)
            form.insertRow(4,card)
            label = form.labelForField(self.depot)
            if label: label.setText('Platform / content')
            self.dlc_list = QListWidget()
            self.dlc_list.setMinimumHeight(240)
            self.dlc_list.setMaximumHeight(320)
            self.dlc_note = QLabel('DLC information loads from Steam when you select a game.')
            self.dlc_note.setWordWrap(True)
            form.insertRow(form.getWidgetPosition(self.depot)[0]+1, 'Optional DLC', self.dlc_list)
            form.insertRow(form.getWidgetPosition(self.dlc_list)[0]+1, self.dlc_note)
            self.depot.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            self.depot.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            self.start_button.setText('Add to queue' if hasattr(parent,'download_queue') else 'Download')
            self.progress_bar = QProgressBar(); self.progress_bar.setRange(0,1000)
            self.progress_bar.setValue(0); self.progress_bar.setFormat('%p%')
            self.progress_info = QLabel('Ready to choose a game')
            form.insertRow(form.rowCount()-1,self.progress_bar)
            form.insertRow(form.rowCount()-1,self.progress_info)
            form.setRowVisible(self.log,False)
            self.log.setMaximumHeight(180)
            self.log.document().setMaximumBlockCount(2000)
            self.depot_info=QLabel('');form.addRow(self.depot_info)
            self.language = QComboBox();self.language.addItem('English','english')
            self.architecture = QComboBox();self.architecture.addItem('64-bit','64');self.architecture.addItem('32-bit','32')
            self.branch = QComboBox();self.branch.addItem('Default (public)','public')
            form.addRow('Language',self.language);form.addRow('Architecture',self.architecture);form.addRow('Branch',self.branch)
            for control in (self.language,self.architecture,self.branch):
                control.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
                control.currentIndexChanged.connect(self.update_dlc_list)
            self.advanced = QPushButton('Advanced'); self.advanced.setCheckable(True)
            def advanced(visible):
                form.setRowVisible(self.provider,visible); form.setRowVisible(self.fetch,visible);form.setRowVisible(self.depot_info,visible)
                for control in (self.language,self.architecture,self.branch):form.setRowVisible(control,visible)
            self.advanced.toggled.connect(advanced); advanced(False)
            self.details_button = QPushButton('Show details'); self.details_button.setCheckable(True)
            def details(visible):
                form.setRowVisible(self.log,visible)
                self.details_button.setText('Hide details' if visible else 'Show details')
            self.details_button.toggled.connect(details)
            self.open_folder = QPushButton('Open folder'); self.open_folder.setEnabled(False)
            self.open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.destination.text())))
            self.add_library = QPushButton('Add to Playlite'); self.add_library.setEnabled(False)
            self.add_library.clicked.connect(self.add_to_library)
            form.setRowVisible(self.start_button,False); form.setRowVisible(self.cancel,False)
            self.start_button.setParent(self);self.cancel.setParent(self)
            footer = QHBoxLayout(); footer.setSpacing(10)
            self.authenticate = QPushButton('Authenticate')
            menu=QMenu(self.authenticate)
            for name in ('Steam','Moon','Hubcap'):
                menu.addAction(name).triggered.connect(lambda checked=False,name=name:self.open_authentication(name))
            self.authenticate.setMenu(menu)
            footer.addWidget(self.advanced);footer.addWidget(self.details_button);footer.addWidget(self.authenticate);footer.addStretch()
            footer.addWidget(self.start_button);footer.addWidget(self.cancel)
            form.addRow(footer);self.start_button.show();self.cancel.show()
            completed = QHBoxLayout();completed.setSpacing(10);completed.addStretch()
            completed.addWidget(self.open_folder);completed.addWidget(self.add_library)
            form.addRow(completed)
            self.status.setText('')
            # Keep room for a wrapped status even while the message is empty.
            self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing()*2)
            self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.depot.currentIndexChanged.connect(self.update_depot_details)
            self.depot.currentIndexChanged.connect(self.update_dlc_list)
            self.auto_started = False
            self.start_button.setEnabled(False)


    def open_search(self):
        if self.authentication or self.busy:return
        if not hasattr(self,'search_picker'):
            self.search_picker=QDialog(self)
            self.search_picker.setWindowTitle('Find a Steam game — Playlite')
            self.search_picker.resize(620,510)
            layout=QVBoxLayout(self.search_picker)
            self.picker_query=QLineEdit()
            self.picker_query.setPlaceholderText('Game name or Steam App ID')
            button=QPushButton('Search')
            button.clicked.connect(self.search_games)
            line=QHBoxLayout();line.setSpacing(10)
            line.addWidget(self.picker_query);line.addWidget(button)
            layout.addLayout(line)
            self.main_form.takeRow(self.search_results)
            self.search_results.setMaximumHeight(16777215)
            layout.addWidget(self.search_results,1)
            self.picker_status=QLabel('Enter at least three characters to search.')
            self.picker_status.setWordWrap(True);layout.addWidget(self.picker_status)
            footer=QHBoxLayout();footer.setSpacing(10);footer.addStretch()
            select=QPushButton('Select game')
            def select_current():
                item=self.search_results.currentItem()
                if item:self.select_game(item)
            select.clicked.connect(select_current)
            close=QPushButton('Cancel');close.clicked.connect(self.search_picker.reject)
            footer.addWidget(select);footer.addWidget(close);layout.addLayout(footer)
            self.picker_query.textEdited.connect(self.queue_search)
            self.picker_query.returnPressed.connect(self.search_games)
            self.search_results.itemClicked.disconnect(self.select_game)
            def finished(result):
                self.search_timer.stop()
                if not result:self.search_generation+=1
            self.search_picker.finished.connect(finished)
        self.picker_query.setText(self.appid.text())
        self.search_picker.open()
        self.picker_query.setFocus();self.picker_query.selectAll()
        self.search_results.show()
        if not self.picker_query.text().strip().isdecimal():self.search_games()

    def current_search_query(self):
        if hasattr(self,'search_picker') and self.search_picker.isVisible():
            return self.picker_query.text().strip()
        return self.appid.text().strip()

    def queue_search(self, query):
        self.search_generation += 1
        self.search_timer.stop()
        self.search_results.clear()
        self.search_results.setVisible(hasattr(self,'search_picker') and self.search_picker.isVisible())
        self.rows=[];self.depot.clear();self.start_button.setEnabled(False)
        if len(query.strip()) >= 3 and not query.strip().isdecimal():
            self.search_timer.start()

    def search_games(self):
        query = self.current_search_query()
        if self.authentication or self.busy: return
        if query.isdecimal():
            self.task(lambda: self.game_search.details(int(query)), self.choose_game)
            return
        if len(query) < 3: return
        if self.searching:
            self.search_timer.start()
            return
        from .plugin import Job
        generation = self.search_generation
        self.searching = True
        result = []
        def operation():
            result.extend(self.game_search.search(query))
            return ''
        job = Job(operation); self.jobs.add(job)
        def done(message):
            self.jobs.discard(job); self.searching = False
            if generation != self.search_generation or query != self.current_search_query(): return
            if message:
                self.status.setText(message)
                if hasattr(self,'picker_status'):self.picker_status.setText(message)
                return
            self.search_results.clear(); self.search_items = {}
            for row in result:
                item = QListWidgetItem(f"{row['name']}\nApp ID: {row['id']}")
                item.setIcon(QIcon(self.cover_placeholder))
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setSizeHint(QSize(0, 90))
                self.search_results.addItem(item); self.search_items[row['id']] = item
            self.search_results.setVisible(True)
            if hasattr(self,'picker_status'):self.picker_status.setText(f'{len(result)} matching games. Double-click a game or choose Select game.' if result else 'No matching games found.')
            self.status.setText(f'{len(result)} matching games. Select a game to use its App ID.' if result else 'No matching games found.')
            self.load_search_covers(result, generation)
        job.signals.finished.connect(done)
        self.status.setText('Searching Steam…')
        if hasattr(self,'picker_status'):self.picker_status.setText('Searching Steam…')
        QThreadPool.globalInstance().start(job)

    def load_search_covers(self, rows, generation):
        from .plugin import Job
        images = {}
        def operation():
            for row in rows:
                if generation != self.search_generation: break
                images[row['id']] = self.game_search.cover(row['id'])
            return ''
        job = Job(operation); self.jobs.add(job)
        def done(_):
            self.jobs.discard(job)
            if generation != self.search_generation: return
            for app, data in images.items():
                pixmap = QPixmap()
                if data and pixmap.loadFromData(data) and app in self.search_items:
                    self.search_items[app].setIcon(QIcon(pixmap))
        job.signals.finished.connect(done)
        QThreadPool.globalInstance().start(job)

    def select_game(self, item):
        self.choose_game(item.data(Qt.ItemDataRole.UserRole))

    def game_app(self):
        query=self.appid.text().strip()
        if query.isdecimal():return int(query)
        if self.selected_app is not None and query==self.game_name:return self.selected_app
        raise ValueError('Search for and select a game first.')

    def choose_game(self, row):
        from .download_flow import game_folder
        self.search_generation += 1
        self.search_timer.stop()
        self.selected_app = row['id']
        self.appid.setText(row['name'])
        self.search_results.clear(); self.search_results.hide()
        self.rows = []; self.content_info = None; self.content_plan = []; self.depot.clear()
        if not self.authentication:
            self.dlc_list.clear(); self.dlc_note.setText('Loading Steam content information…')
        self.game_name = row['name']
        if hasattr(self,'search_picker'):self.search_picker.accept()
        if not self.authentication:
            self.game_title.setText(f"<b>{__import__('html').escape(self.game_name)}</b><br>Steam App ID: {row['id']}")
            self.destination.setText(str(game_folder(self.default_root,self.game_name)))
            self.game_cover.clear()
            cover = self.game_search.cache.get(row['id'], b'')
            pixmap = QPixmap()
            if cover and pixmap.loadFromData(cover):
                self.game_cover.setPixmap(pixmap.scaled(self.game_cover.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            self.load_selected_cover(row['id'])
            self.fetch_pack()

    def load_selected_cover(self, app):
        from .plugin import Job
        generation=self.search_generation
        image=[]
        def operation():
            image.append(self.game_search.cover(app));return ''
        job=Job(operation);self.jobs.add(job)
        def done(_):
            self.jobs.discard(job)
            if generation != self.search_generation:return
            pixmap=QPixmap()
            if image and image[0] and pixmap.loadFromData(image[0]):
                self.game_cover.setPixmap(pixmap.scaled(self.game_cover.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        job.signals.finished.connect(done);QThreadPool.globalInstance().start(job)

    def open_authentication(self,name):
        if self.busy:return
        if name=='Steam':
            DownloadDialog(self.network,self,authentication='steam').exec()
            self.username.setText(QSettings('Playlite','SteamDownloader').value('steam_account',''))
        else:
            from .authentication_dialog import CredentialDialog
            CredentialDialog(name,self.network,self).exec()

    def update_depot_details(self):
        if not self.authentication and self.content_info:
            from .app_info import build_plan
            try:
                plan=build_plan(self.content_info,{self.pack_app:self.rows},self.depot.currentData(),set())
                self.depot_info.setText('Base-game depots: '+', '.join(str(row.id) for _,row,_ in plan))
            except ValueError as error:
                self.depot_info.setText(str(error))
            self.depot.setToolTip(self.depot_info.text())


    def showEvent(self,event):
        super().showEvent(event)
        if not self.authentication and not self.auto_started:
            self.auto_started=True
            QTimer.singleShot(0,self.connect_vpn)

    def add_to_library(self):
        parent=self.parentWidget()
        if not parent or not hasattr(parent,'data'):
            self.status.setText('Open this downloader from the Playlite menu to add a game.');return
        executable,_=QFileDialog.getOpenFileName(self,'Choose the game executable',self.destination.text())
        if not executable:return
        from playlite.add_game import AddGameEditor
        from playlite.app import save_game
        game={'Id':str(uuid.uuid4()),'Name':self.game_name,'InstallDirectory':self.destination.text(),
              'Executable':executable,'Platforms':[],'IsInstalled':True,'MetadataIds':{'SteamMetadata':str(self.game_app())}}
        dialog=AddGameEditor(executable,parent.data,self,game=game)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            parent.games=save_game(parent.data,parent.games,dialog.result_game)
            parent.focus_added_game(dialog.result_game['Id'])
            for plugin in dialog.generic_plugins:plugin.after_game_added(parent,dialog.result_game,dialog)
            for cache in dialog.download_caches:cache.cleanup()
            self.status.setText('Game added to Playlite.')

    def connect_vpn(self):
        if self.busy:return
        self.vpn_connecting=True
        self.cancel.setText('Cancel connection')
        if not self.network.container:self.network.cancelled.clear()
        from .credentials import Wallet
        from .settings import Preferences
        def operation(progress):
            if self.network.container:return str(self.network.check())
            settings = QSettings('Playlite', 'SteamDownloader')
            preferences = Preferences(settings.value('country', ''), settings.value('protocol', 'udp')).validate()
            self.owns_connection = not self.authentication
            return self.network.connect(preferences,*Wallet().read(),progress=progress)
        def done(message):
            self.status.setText('')
            if self.authentication:
                self.status.setText(message);self.steam_form.setRowVisible(self.vpn_button,False)
            else:
                self.vpn_status.setText(message.split('. Isolated public IP:')[0])
                self.vpn_connect.hide()
        self.task(operation, done, progress=True)

    def save_key(self):
        key = self.key.text().strip()
        if not key or any(c in key for c in '\r\n\0'):
            self.status.setText('Enter a valid Hubcap API key.'); return
        self.key.clear()
        self.task(lambda: HubcapKeyWallet().save({'key':key}), lambda _: self.status.setText('Hubcap API key saved in KWallet.'))

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select empty download folder', self.destination.text())
        if folder: self.destination.setText(folder)

    def set_content_enabled(self, enabled):
        if self.authentication: return
        for control in (self.appid,self.provider,self.depot,self.destination,self.dlc_list,self.language,self.architecture,self.branch):
            control.setEnabled(enabled)

    def task(self, operation, completed, progress=False, on_error=None):
        if self.busy: return
        from .plugin import Job
        self.busy = True; self.set_content_enabled(False); self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        result = []
        def work():
            result.append(operation(job.signals.progress.emit) if progress else operation())
            return 'Done.'
        job = Job(work); self.jobs.add(job)
        def done(message):
            self.busy = False; self.set_content_enabled(True); self.jobs.discard(job)
            if getattr(self,'vpn_connecting',False):
                self.vpn_connecting=False;self.cancel.setText('Close')
            self.fetch.setEnabled(True); self.start_button.setEnabled(bool(self.authentication or self.rows))
            if result:
                try: completed(result[0])
                except Exception as error: self.status.setText(str(error))
            else:
                self.status.setText(message)
                if on_error:on_error(message)
        job.signals.finished.connect(done)
        job.signals.progress.connect(self.status.setText)
        self.status.setText('Working through the isolated VPN…')
        QThreadPool.globalInstance().start(job)

    def login(self):
        code = self.code.text(); self.code.clear()
        self.task(lambda: self.moon.login(code), self.status.setText)

    def fetch_pack(self):
        try:
            app = self.game_app()
            source = self.provider.currentText()
            username = self.username.text().strip()
        except Exception as error:
            self.status.setText(str(error)); return
        self.rows=[]; self.content_info=None; self.content_plan=[]
        if not self.authentication:
            self.dlc_list.clear(); self.dlc_note.setText('Loading Steam content information…')
        def operation():
            from .app_info import fetch_app_info,resolve_shared
            info = resolve_shared(fetch_app_info(self.network, app, username),lambda shared:fetch_app_info(self.network,shared,username))
            credential = self.moon.token() if source == 'Luie' else (HubcapKeyWallet().read() or {}).get('key','') if source == 'Hubcap' else ''
            rows=self.transport.fetch(source, app, credential)
            from .dlc_names import resolve_names
            return rows, resolve_names(info,rows,self.game_search)
        def done(result):
            rows, info = result
            QSettings('Playlite','SteamDownloader').setValue('manifest_provider',source)
            if source == 'Hubcap': self.network.hubcap_confirmed = True
            self.rows = rows; self.pack_app = app; self.content_info = info
            self.pack_source = source
            from .app_info import depot_entries
            language_values={'english'}
            for entry in [info['game'],*info['dlc']]:
                language_values.update(str(row.get('config',{}).get('language','')).lower() for row in depot_entries(entry).values())
            self.language.blockSignals(True);self.language.clear()
            for language in sorted(language_values-{''}):self.language.addItem(language.replace('_',' ').title(),language)
            self.language.setCurrentIndex(self.language.findData('english'));self.language.blockSignals(False)
            branches=(info['game'].get('depots') or {}).get('branches',{}) if isinstance(info['game'].get('depots'),dict) else {}
            self.branch.blockSignals(True);self.branch.clear();self.branch.addItem('Default (public)','public')
            if isinstance(branches,dict):
                for name,settings in branches.items():
                    if name!='public' and isinstance(settings,dict) and str(settings.get('pwdrequired','0'))!='1':self.branch.addItem(name,name)
            self.branch.blockSignals(False)
            self.depot.blockSignals(True); self.depot.clear()
            from .app_info import content_depots, matches_platform
            advertised={os.strip() for row in content_depots(info).values() for os in str(row.get('config',{}).get('oslist','')).split(',') if os.strip()}
            for platform, label in (('linux','Linux'),('windows','Windows'),('macos','macOS')):
                if (not advertised or platform in advertised) and any(not str(row.get('config',{}).get('oslist','')) or platform in str(row.get('config',{}).get('oslist','')).split(',') for row in content_depots(info).values()):
                    self.depot.addItem(label,platform)
            self.depot.blockSignals(False)
            self.default_dlc_selection={entry['id'] for entry in info['dlc'] if entry.get('owned') is True}
            self.update_depot_details(); self.update_dlc_list()
            self.status.setText('')
            self.start_button.setEnabled(bool(rows) and self.depot.count()>0)
            self.progress_info.setText('Ready to download base game and selected DLC')
        self.task(operation, done)

    def update_dlc_list(self, *_):
        if self.authentication or not self.content_info: return
        from .app_info import content_depots, matches_platform, manifest_id
        selected = {self.dlc_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.dlc_list.count()) if self.dlc_list.item(i).checkState()==Qt.CheckState.Checked}
        selected.update(getattr(self,'default_dlc_selection',set()))
        self.default_dlc_selection=set()
        self.dlc_list.clear()
        platform = self.depot.currentData()
        for entry in self.content_info['dlc']:
            depots = content_depots(self.content_info,entry)
            applicable = any(matches_platform(row,platform,self.language.currentData(),self.architecture.currentData()) for row in depots.values())
            reason = 'Included in base-game files' if not depots else 'Unavailable for these content settings' if not applicable else 'Manifest request failed' if entry.get('manifest_error') and entry.get('manifest_provider')==getattr(self,'pack_source',None) else ''
            item=QListWidgetItem(entry['name'] or f'Unknown DLC ({entry["id"]})')
            item.setData(Qt.ItemDataRole.UserRole,entry['id'])
            required={key for key,row in depots.items() if matches_platform(row,platform,self.language.currentData(),self.architecture.currentData())}
            available={row.id for row in self.rows}
            manifests={row.id:row.manifest for row in self.rows}
            try:expected={key:manifest_id(row,self.branch.currentData() or 'public') for key,row in depots.items() if key in required}
            except ValueError:expected={};reason=reason or 'Protected branch unsupported'
            if not reason and any(key in manifests and gid is not None and manifests[key]!=gid for key,gid in expected.items()):reason='Manifest version mismatch'
            failures=self.content_info.get('_cdn_failures',{})
            failed=[failures[key][1] for key in required if key in failures and expected.get(key)==failures[key][0]]
            if not reason and failed:reason='CDN access check failed'
            availability=failed[0] if reason=='CDN access check failed' else entry.get('manifest_error','') if reason=='Manifest request failed' else reason or ('Required manifests are available. Steam/CDN access has not been tested.' if required<=available else 'Extra manifests are needed; the provider will be checked before queueing. Steam/CDN access has not been tested.')
            checks=self.content_info.get('_cdn_checks',{})
            if not reason and required and all(key in checks and checks[key][1]=='OK' and (key not in manifests or checks[key][0]==manifests[key]) for key in required):
                availability='Steam/CDN sample verified. This does not guarantee completion of the full download.'
            item.setToolTip(('Name could not be resolved from accessible metadata sources. ' if not entry['name'] else '')+availability+(' Refresh the manifest pack under Advanced to retry.' if reason=='Manifest request failed' else ''))
            if reason:item.setText(item.text()+' · '+reason)
            if reason in ('Included in base-game files','Unavailable for these content settings'):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if entry['id'] in selected else Qt.CheckState.Unchecked)
            self.dlc_list.addItem(item)
        self.dlc_list.setVisible(bool(self.content_info['dlc']))
        self.dlc_note.setText('Select optional DLC. Base-game files are always included.' if self.content_info['dlc'] else 'Steam lists no DLC for this game.')

    def prepare_download(self):
        if self.authentication: return self.download()
        if self.busy: return
        try:
            if not self.content_info or self.game_app() != self.pack_app:
                raise ValueError('Choose a game and load its content information first.')
            if not self.destination.text(): raise ValueError('Choose a download folder.')
            destination=Path(self.destination.text()).expanduser().resolve()
            if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
                raise ValueError('Choose an empty download folder.')
            info=self.content_info; source=self.pack_source; platform=self.depot.currentData()
            selected={self.dlc_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.dlc_list.count()) if self.dlc_list.item(i).checkState()==Qt.CheckState.Checked}
            packs={self.pack_app:list(self.rows)}
            language=self.language.currentData();architecture=self.architecture.currentData();branch=self.branch.currentData()
            username=self.username.text().strip()
        except Exception as error:
            self.status.setText(str(error)); return
        def operation(progress):
            from .app_info import prepare_content
            credential=self.moon.token() if source=='Luie' else (HubcapKeyWallet().read() or {}).get('key','') if source=='Hubcap' else ''
            plan=prepare_content(info,packs[self.pack_app],platform,selected,source,
                                   lambda app:self.transport.fetch(source,app,credential),language,architecture,branch)
            from .preflight import check_plan,PreflightFailure
            try:states=check_plan(self.network,plan,username,branch,progress)
            except PreflightFailure as error:
                info.setdefault('_cdn_failures',{})[error.depot]=(error.manifest,str(error))
                raise
            return plan,states
        def done(result):
            plan,states=result
            self.preflight_states=states
            for _,row,_ in plan:self.content_info.get('_cdn_failures',{}).pop(row.id,None)
            self.content_info['_cdn_checks']={row.id:(row.manifest,state) for (_,row,_),state in zip(plan,states)}
            self.update_dlc_list()
            self.content_plan=plan; self.content_index=0
            self.total_downloaded=0; self.total_uncompressed=0; self.batch_started_at=time.monotonic()
            window=self.parentWidget()
            if hasattr(window,'download_queue'):
                from .queue_runner import QueueRunner
                network=self.network
                snapshot={'app':self.pack_app,'rows':list(self.rows),'plan':list(plan),'info':self.content_info,'username':self.username.text(),'branch':branch}
                try:
                    window.download_queue.enqueue(self.game_name or self.content_info['game']['name'],self.destination.text(),
                        lambda queue,entry:QueueRunner(network,window,queue,entry,snapshot))
                except ValueError as error:
                    self.status.setText(str(error));return
                self.owns_connection=False
                self.network.download_queue=window.download_queue
                self.status.setText('Added to Downloads. You can choose another game or close this window.')
                window.downloads_panel.set_open(True)
            else:self.download()
        def failed(message):
            self.progress_info.setText('Nothing added to queue. Manifest preparation failed.')
            self.update_dlc_list()
        self.task(operation,done,progress=True,on_error=failed)

    def download(self):
        if self.busy: return
        stage = None
        if self.authentication == 'steam':
            try:
                self.network.check()
            except Exception:
                self.status.setText('Connect the isolated VPN before signing in.')
                self.steam_form.setRowVisible(self.vpn_button, True)
                return
            self.pending_password = self.password.text()
            self.password.clear()
            self.steam_form.setRowVisible(self.guard_row, False)
        try:
            if not self.authentication:
                app = self.game_app()
                if not self.rows or app != self.pack_app: raise ValueError('Fetch the manifest pack for this App ID first.')
                if not self.destination.text():raise ValueError('Choose a download folder.')
                destination = Path(self.destination.text()).expanduser().resolve()
            if not self.username.text().strip(): raise ValueError('Enter your Steam account name.')
            row = self.rows[self.depot.currentIndex()] if not self.authentication else None
            if not self.authentication and self.content_plan:
                app,row,content_name=self.content_plan[self.content_index]
            self.temporary = tempfile.TemporaryDirectory(prefix='playlite-depot-input-')
            pack = None
            if row is not None:
                pack = Path(self.temporary.name) / 'pack'; prepare_depot(row, pack)
            self.steam_wallet = SteamSessionWallet(self.username.text())
            saved = self.steam_wallet.read()
            if not self.authentication and saved is None:
                raise ValueError('Sign into Steam in plugin settings → Steam login before downloading.')
            auth = Path(self.temporary.name) / 'auth'
            auth.mkdir(mode=0o777); auth.chmod(0o777)
            self.auth_file = auth / 'account.config'
            if saved is not None:
                data = base64.b64decode(saved['data'], validate=True)
                if len(data) > 4 * 1024 * 1024: raise ValueError('Saved Steam session exceeds the size limit.')
                self.auth_file.write_bytes(data); self.auth_file.chmod(0o666)
            # Keep incomplete output in a separate staging folder, never overwrite files.
            if self.authentication:
                stage=Path(self.temporary.name)/'output';stage.mkdir(mode=0o777);stage.chmod(0o777)
            else:
                from .download_flow import prepare_destination
                if self.content_plan and self.content_index>0:
                    destination,stage=self.download_destination,self.download_staging
                else:
                    destination,stage=prepare_destination(destination)
                self.download_destination=destination;self.download_staging=stage
            args = worker_args(self.network, Request(app, row.id, row.manifest) if row is not None else Request(1, 1), stage, pack=pack, username=self.username.text())
            args[args.index('--workdir') + 1] = '/auth'
            index = args.index('playlite-depot-worker:test')
            args[index:index] = ['--mount', f'type=bind,src={auth},dst=/auth']
            if self.authentication:
                import secrets
                index = args.index('playlite-depot-worker:test')
                args[index+1:] = ['-login-only', '-username', self.username.text().strip(), '-loginid', str(secrets.randbelow(2**32-1)+1)]
            args.append('-remember-password')
            if not self.authentication:args+=['-branch',self.branch.currentData() or 'public']
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
            if self.authentication == 'steam': self.pending_password = ''
            self.status.setText(str(error)); return
        QSettings('Playlite', 'SteamDownloader').setValue('steam_account', self.username.text().strip())
        self.busy = True; self.set_content_enabled(False); self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        self.cancel.setText('Cancel login' if self.authentication else 'Cancel download')
        self.output = ''; self.auth_needed = False; self.log.clear()
        self.started_at=time.monotonic()
        if not self.authentication:
            self.progress_bar.setFormat(f'Part {self.content_index+1}/{len(self.content_plan)} · %p%' if len(self.content_plan)>1 else '%p%')
            self.progress_bar.setRange(0,0);self.progress_info.setText('Connecting to Steam…')
            self.open_folder.setEnabled(False);self.add_library.setEnabled(False)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start('docker', args)
        self.status.setText('Signing in to Steam…' if self.authentication else f'Downloading {content_name} · Part {self.content_index+1} of {len(self.content_plan)}' if self.content_plan else 'Downloading…')

    def read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        prompt_text = self.output[-180:] + text
        self.output += text
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()
        if not self.authentication:
            matches=re.findall(r'(\d+(?:\.\d+)?)%\s+/output/',prompt_text)
            if matches:
                percent=min(100,float(matches[-1]))
                self.progress_bar.setRange(0,1000);self.progress_bar.setValue(int(percent*10))
                elapsed=max(.1,time.monotonic()-getattr(self,'started_at',time.monotonic()))
                remaining=int(elapsed*(100-percent)/percent) if percent>0 else None
                self.progress_info.setText(f'{percent:.1f}% · '+ (f'About {remaining//60}m {remaining%60}s remaining' if remaining is not None else 'Downloading…'))

        if not self.authentication:
            telemetry=re.findall(r'PLAYLITE_PROGRESS (\d+) (\d+) (\d+)\r?\n',prompt_text)
            if telemetry:
                downloaded,total,transferred=map(int,telemetry[-1])
                elapsed=max(.1,time.monotonic()-self.started_at)
                percent=100*downloaded/total if total else 0
                self.progress_bar.setRange(0,1000);self.progress_bar.setValue(min(1000,int(percent*10)))
                remaining=int(elapsed*(total-downloaded)/downloaded) if downloaded else 0
                self.progress_info.setText(f'{downloaded/1000000:.1f} / {total/1000000:.1f} MB · {transferred/elapsed/1000000:.1f} MB/s average · About {max(0,remaining)//60}m {max(0,remaining)%60}s remaining')
        code_prompt = re.search(r'please enter (?:your |the )?(?:2 factor )?(?:auth(?:entication)? )?code', prompt_text, re.IGNORECASE)
        if self.authentication and text:
            if code_prompt:
                self.status.setText('Enter the Steam Guard code sent to your email.' if 'email' in prompt_text[code_prompt.start():].lower() else 'Enter your Steam Guard code from your authenticator app.')
                if self.authentication == 'steam':
                    self.steam_form.setRowVisible(self.guard_row, True)
                    self.response.setPlaceholderText('Steam Guard code')
                    self.response.setFocus()
            elif 'Enter account password' in prompt_text:
                if self.authentication == 'steam' and self.pending_password:
                    self.process.write((self.pending_password + '\n').encode())
                    self.pending_password = ''
                else:
                    self.status.setText('Enter your Steam password below.')
                    if self.authentication == 'steam':
                        self.steam_form.setRowVisible(self.guard_row, True)
                        self.response.setPlaceholderText('Steam password')
            elif 'Use the Steam Mobile App' in prompt_text:
                self.status.setText('Approve this sign-in in the Steam mobile app. Waiting for approval…')
                if self.authentication == 'steam':
                    self.steam_form.setRowVisible(self.guard_row, False)
        prompts = ('Enter account password', 'Please enter your 2 factor auth code', 'Please enter the authentication code')
        if not self.authentication and not getattr(self, 'auth_needed', False) and (code_prompt or any(prompt in self.output for prompt in prompts)):
            self.auth_needed = True
            self.close_or_cancel()

    def send_response(self):
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.process.write((self.response.text() + '\n').encode())
        self.response.clear()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.finished(1)

    def finished(self, code, *_):
        self.read_output()
        continue_download = False
        try:
            if self.authentication:
                if code: raise RuntimeError('Steam authentication failed or was cancelled. See the login log.')
                if not self.auth_file.is_file(): raise RuntimeError('Steam did not create a reusable session. See the login log.')
                self.network.steam_confirmed = True
                self.status.setText('Steam authentication completed.')
            else:
                require_download_success(code, self.output)
                totals=re.findall(r'Total downloaded: (\d+) bytes \((\d+) bytes uncompressed\)',self.output)
                if totals:
                    self.total_downloaded+=int(totals[-1][0]); self.total_uncompressed+=int(totals[-1][1])
                from .download_flow import finalize_download
                if self.content_plan and self.content_index+1 < len(self.content_plan):
                    self.content_index += 1
                    continue_download = True
                else:
                    finalize_download(self.download_destination,self.download_staging)
                    continue_download = False
                if continue_download:
                    self.status.setText(f'Validated content {self.content_index} of {len(self.content_plan)}.')
                else:
                    self.complete_download()

        except Exception as error:
            self.status.setText(str(error) + ('' if self.authentication else ' Incomplete files remain in staging.'))
            if not self.authentication:
                self.progress_bar.setRange(0,1000)
                self.progress_info.setText('Download stopped')
        if self.temporary:
            try:
                if self.auth_file.is_file():
                    data = self.auth_file.read_bytes()
                    if len(data) > 4 * 1024 * 1024: raise ValueError('Steam session exceeds the size limit.')
                    self.steam_wallet.save({'data':base64.b64encode(data).decode()})
            except Exception:
                self.status.setText(self.status.text() + ' Steam login could not be saved in KWallet.')
            finally:
                self.temporary.cleanup(); self.temporary = None
        self.busy = False; self.set_content_enabled(True); self.fetch.setEnabled(True); self.start_button.setEnabled(True)
        self.cancel.setText('Close')
        if self.authentication == 'steam':
            self.pending_password = ''
            self.steam_form.setRowVisible(self.guard_row, False)
            self.login_succeeded = code == 0 and 'could not be saved' not in self.status.text() and self.status.text() == 'Steam authentication completed.'
            self.network.steam_confirmed = self.login_succeeded
            if self.login_succeeded:
                self.status.setText(f'Signed in as {self.username.text().strip()}.')
                self.steam_form.setRowVisible(self.password, False)
                self.start_button.hide()
                self.cancel.setText('Done')
            else:
                self.start_button.setText('Try again')

        if continue_download:
            self.download()

        if getattr(self, 'auth_needed', False):
            self.status.setText('Steam requires a new login. Open plugin settings → Steam login and sign in again. Incomplete files were retained.')

    def complete_download(self):
        self.progress_bar.setFormat('%p%')
        self.progress_bar.setRange(0,1000);self.progress_bar.setValue(1000)
        elapsed=max(.1,time.monotonic()-(self.batch_started_at or self.started_at))
        self.progress_info.setText(f'{self.total_uncompressed/1000000:.1f} MB · {self.total_downloaded/elapsed/1000000:.1f} MB/s average' if self.total_uncompressed else 'Files validated')
        self.status.setText('Download complete. Files are ready in your chosen folder.')
        self.open_folder.setEnabled(True);self.add_library.setEnabled(True)

    def forget_steam(self):
        if self.busy: return
        if not self.username.text().strip():
            self.status.setText('Enter the Steam account name whose saved login you want to remove.'); return
        username = self.username.text()
        self.network.steam_confirmed = False
        self.task(lambda: SteamSessionWallet(username).clear(), lambda _: self.status.setText('Saved Steam login removed from KWallet.'))

    def close_or_cancel(self):
        if getattr(self,'vpn_connecting',False):
            self.network.cancel()
            self.status.setText('Cancelling the VPN connection…')
            return
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.status.setText('Stopping the download worker…')
            self.stopper = QProcess(self)
            self.stopper.start('docker', ['rm', '-f', self.container])
        elif not self.busy: self.close()

    def closeEvent(self, event):
        if self.busy:
            self.close_or_cancel(); event.ignore(); return
        self.search_generation += 1
        self.search_timer.stop()
        self.moon.session = None
        self.rows = []
        queue=vars(self.network).get('download_queue')
        queued=queue is not None and any(row.state in ('Queued','Downloading') for row in queue.entries)
        if self.owns_connection and not queued:
            self.network.cancel()
            from .plugin import Job
            job=Job(self.network.disconnect)
            self.jobs.add(job)
            job.signals.finished.connect(lambda _:self.jobs.discard(job))
            QThreadPool.globalInstance().start(job)
        self.key.clear(); self.response.clear()
        if self.authentication == 'steam':
            self.password.clear(); self.pending_password = ''
        super().closeEvent(event)
