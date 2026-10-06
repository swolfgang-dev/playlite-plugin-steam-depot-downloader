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
        self.game_name = ''
        self.owns_connection = False
        self.jobs = set()
        self.process = None
        self.temporary = None
        self.container = None
        self.output = ''
        self.setWindowTitle('Steam Depot Downloader — Playlite')
        self.resize(850, 720)
        form = QFormLayout(self)
        self.main_form = form
        form.setHorizontalSpacing(14); form.setVerticalSpacing(12)
        auth_rows = []; download_rows = []
        notice = QLabel('Downloads use a separate Steam session inside the VPN. Use an account that owns the game. Moon login is saved in KWallet and restored automatically. Download one selected depot at a time; the destination must be empty.')
        notice.setWordWrap(True)
        form.addRow(notice)
        self.appid = QLineEdit('736260')
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
        self.start_button = QPushButton('Download selected depot'); self.start_button.clicked.connect(self.download)
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
            self.depot.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            self.depot.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            self.start_button.setText('Download')
            self.progress_bar = QProgressBar(); self.progress_bar.setRange(0,1000)
            self.progress_bar.setValue(0); self.progress_bar.setFormat('%p%')
            self.progress_info = QLabel('Ready to choose a game')
            form.insertRow(form.rowCount()-1,self.progress_bar)
            form.insertRow(form.rowCount()-1,self.progress_info)
            form.setRowVisible(self.log,False)
            self.log.setMaximumHeight(180)
            self.log.document().setMaximumBlockCount(2000)
            self.depot_info=QLabel('');form.addRow(self.depot_info)
            self.advanced = QPushButton('Advanced'); self.advanced.setCheckable(True)
            def advanced(visible):
                form.setRowVisible(self.provider,visible); form.setRowVisible(self.fetch,visible);form.setRowVisible(self.depot_info,visible)
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
            self.depot.currentIndexChanged.connect(self.update_depot_details)
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

    def choose_game(self, row):
        from .download_flow import game_folder
        self.search_generation += 1
        self.search_timer.stop()
        self.appid.setText(str(row['id']))
        self.search_results.clear(); self.search_results.hide()
        self.rows = []; self.depot.clear()
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
        index=self.depot.currentIndex()
        if index >= 0 and index < len(self.rows):
            row=self.rows[index]
            self.depot.setToolTip(f'Depot {row.id} · Manifest {row.manifest}')
            if not self.authentication:self.depot_info.setText(self.depot.toolTip())

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
              'Executable':executable,'Platforms':[],'IsInstalled':True,'MetadataIds':{'SteamMetadata':self.appid.text()}}
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

    def task(self, operation, completed, progress=False):
        if self.busy: return
        from .plugin import Job
        self.busy = True; self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        result = []
        def work():
            result.append(operation(job.signals.progress.emit) if progress else operation())
            return 'Done.'
        job = Job(work); self.jobs.add(job)
        def done(message):
            self.busy = False; self.jobs.discard(job)
            if getattr(self,'vpn_connecting',False):
                self.vpn_connecting=False;self.cancel.setText('Close')
            self.fetch.setEnabled(True); self.start_button.setEnabled(bool(self.authentication or self.rows))
            if result:
                try: completed(result[0])
                except Exception as error: self.status.setText(str(error))
            else: self.status.setText(message)
        job.signals.finished.connect(done)
        job.signals.progress.connect(self.status.setText)
        self.status.setText('Working through the isolated VPN…')
        QThreadPool.globalInstance().start(job)

    def login(self):
        code = self.code.text(); self.code.clear()
        self.task(lambda: self.moon.login(code), self.status.setText)

    def fetch_pack(self):
        try:
            app = int(self.appid.text())
            source = self.provider.currentText()
            credential = self.key.text() if source == 'Hubcap' else ''
        except Exception as error:
            self.status.setText(str(error)); return
        def done(rows):
            QSettings('Playlite','SteamDownloader').setValue('manifest_provider',source)
            if source == 'Hubcap': self.network.hubcap_confirmed = True
            self.rows = rows; self.pack_app = app
            self.depot.clear()
            for row in rows:
                name=row.name.lower()
                label = 'Linux' if 'linux' in name else 'macOS' if 'osx' in name or 'macos' in name else 'Windows' if 'windows' in name else row.name or 'Game files'
                self.depot.addItem(label)
            preferred=next((i for i,row in enumerate(rows) if 'linux' in row.name.lower()),0)
            self.depot.setCurrentIndex(preferred)
            self.update_depot_details()
            self.status.setText('Ready to download.' if self.authentication else '')
            if not self.authentication:
                self.start_button.setEnabled(bool(rows))
                self.progress_info.setText('Ready to download')
        self.task(lambda: self.transport.fetch(source, app, self.moon.token() if source == 'Luie' else (HubcapKeyWallet().read() or {}).get('key', '') if source == 'Hubcap' else credential), done)

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
                app = int(self.appid.text())
                if not self.rows or app != self.pack_app: raise ValueError('Fetch the manifest pack for this App ID first.')
                if not self.destination.text():raise ValueError('Choose a download folder.')
                destination = Path(self.destination.text()).expanduser().resolve()
            if not self.username.text().strip(): raise ValueError('Enter your Steam account name.')
            row = self.rows[self.depot.currentIndex()] if not self.authentication else None
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
        self.busy = True; self.fetch.setEnabled(False); self.start_button.setEnabled(False)
        self.cancel.setText('Cancel login' if self.authentication else 'Cancel download')
        self.output = ''; self.auth_needed = False; self.log.clear()
        self.started_at=time.monotonic()
        if not self.authentication:
            self.progress_bar.setRange(0,0);self.progress_info.setText('Connecting to Steam…')
            self.open_folder.setEnabled(False);self.add_library.setEnabled(False)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start('docker', args)
        self.status.setText('Signing in to Steam…' if self.authentication else 'Downloading…')

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
        try:
            if self.authentication:
                if code: raise RuntimeError('Steam authentication failed or was cancelled. See the login log.')
                if not self.auth_file.is_file(): raise RuntimeError('Steam did not create a reusable session. See the login log.')
                self.network.steam_confirmed = True
                self.status.setText('Steam authentication completed.')
            else:
                require_download_success(code, self.output)
                from .download_flow import finalize_download
                finalize_download(self.download_destination,self.download_staging)
                self.progress_bar.setRange(0,1000);self.progress_bar.setValue(1000)
                totals=re.findall(r'Total downloaded: (\d+) bytes \((\d+) bytes uncompressed\)',self.output)
                elapsed=max(.1,time.monotonic()-self.started_at)
                self.progress_info.setText(f'{int(totals[-1][1])/1000000:.1f} MB · {int(totals[-1][0])/elapsed/1000000:.1f} MB/s average' if totals else 'Files validated')
                self.status.setText('Download complete. Files are ready in your chosen folder.')
                self.open_folder.setEnabled(True);self.add_library.setEnabled(True)
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
        self.busy = False; self.fetch.setEnabled(True); self.start_button.setEnabled(True)
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

        if getattr(self, 'auth_needed', False):
            self.status.setText('Steam requires a new login. Open plugin settings → Steam login and sign in again. Incomplete files were retained.')

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
        if self.owns_connection:
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
