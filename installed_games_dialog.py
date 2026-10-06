"""Manage content in the private Steam installation through native Steam actions."""
import time
from PyQt6.QtCore import Qt,QThreadPool,QTimer
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QMessageBox
from .steam_runtime import SteamRuntime


def queued_app(network,appid):
    queue=vars(network).get('download_queue')
    if queue is None:return False
    return any(row.state in ('Queued','Downloading') and (getattr(row,'steam_appid',None)==appid or getattr(getattr(row,'controller',None),'snapshot',{}).get('app')==appid) for row in queue.entries)


class InstalledGamesDialog(QDialog):
    def __init__(self,network,parent=None):
        super().__init__(parent)
        self.network=network;self.runtime=SteamRuntime(network);self.job=None;self.games=[]
        self.setWindowTitle('Installed Steam content — Playlite');self.resize(820,460)
        layout=QVBoxLayout(self)
        note=QLabel('Manage games and tools in isolated Steam. Uninstalling here keeps the copies exported to your Playlite download folders.')
        note.setWordWrap(True);layout.addWidget(note)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['Name','Size','Status','Steam library'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.update_buttons);layout.addWidget(self.table)
        self.status=QLabel('Loading installed content…');self.status.setWordWrap(True);layout.addWidget(self.status)
        controls=QHBoxLayout();self.refresh_button=QPushButton('Refresh');self.refresh_button.clicked.connect(self.refresh)
        self.uninstall_button=QPushButton('Uninstall…');self.uninstall_button.clicked.connect(self.uninstall)
        self.close_button=QPushButton('Close');self.close_button.clicked.connect(self.close)
        controls.addWidget(self.refresh_button);controls.addWidget(self.uninstall_button);controls.addStretch();controls.addWidget(self.close_button);layout.addLayout(controls)
        self.update_buttons();QTimer.singleShot(0,self.refresh)

    def selected(self):
        row=self.table.currentRow()
        return self.games[row] if 0<=row<len(self.games) else None

    def update_buttons(self):
        game=self.selected();busy=self.job is not None
        self.refresh_button.setEnabled(not busy);self.close_button.setEnabled(not busy)
        self.uninstall_button.setEnabled(not busy and game is not None and game['status']!='Downloading' and not queued_app(self.network,game['appid']))

    def task(self,operation,after):
        if self.job:return
        from .plugin import Job
        result=[]
        def run():result.append(operation());return 'Done'
        self.job=Job(run);job=self.job;self.update_buttons()
        def finished(message):
            self.job=None;self.update_buttons()
            if result:after(result[0])
            else:self.status.setText(message)
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)

    def refresh(self):
        if self.job:return
        self.status.setText('Querying isolated Steam…')
        self.task(lambda:self.runtime.request('installed_games'),self.populate)

    def populate(self,result):
        self.games=result.get('games',[]);self.table.setRowCount(len(self.games))
        for row,game in enumerate(self.games):
            values=[game['name'],f'{game["size"]/1024**3:.2f} GiB',game['status'],game['library']]
            for column,value in enumerate(values):self.table.setItem(row,column,QTableWidgetItem(value))
        self.status.setText(f'{len(self.games)} installed items in isolated Steam.');self.update_buttons()

    def uninstall(self):
        game=self.selected()
        if self.job or game is None:return
        if game['status']=='Downloading' or queued_app(self.network,game['appid']):
            self.status.setText('Cancel this game’s queued or active download before uninstalling it.');return
        answer=QMessageBox.question(self,'Uninstall from isolated Steam?',f'Uninstall {game["name"]} from isolated Steam?\n\nIts private Steam files will be removed. Your exported download folder will be kept.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.status.setText('Uninstalling '+game['name']+'…')
        def operation():
            self.runtime.request('uninstall',game['appid'])
            deadline=time.monotonic()+120
            while True:
                result=self.runtime.request('installed_games')
                if all(row['appid']!=game['appid'] for row in result.get('games',[])):return result
                if time.monotonic()>=deadline:raise RuntimeError('Steam has not completed the uninstall. Refresh the list or check its isolated desktop for an error.')
                time.sleep(1)
        def finished(result):self.populate(result);self.status.setText(game['name']+' was uninstalled from isolated Steam.')
        self.task(operation,finished)

    def closeEvent(self,event):
        if self.job:event.ignore();return
        owner=self.parentWidget()
        while owner is not None:
            if (hasattr(owner,'settings_closed') and not owner.settings_closed) or getattr(owner,'network',None) is self.network:break
            owner=owner.parentWidget()
        if owner is None:
            from .vpn_lifecycle import disconnect_when_idle
            disconnect_when_idle(self.network)
        super().closeEvent(event)
