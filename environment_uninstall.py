"""Optional removal of resources owned by this Steam Downloader profile."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QLabel,QCheckBox,QPlainTextEdit,QPushButton,QHBoxLayout
from .constants import LABEL,WORKER_LABEL,OWNER_LABEL,OWNER
from .steam_runtime import SteamRuntime,RUNTIME_IMAGE


def remove_environment(network,delete_data=False,progress=lambda message:None,allow_legacy=False):
    from .vpn_lifecycle import has_downloads
    if has_downloads(network):raise RuntimeError('Pause or cancel downloads before removing Steam.')
    runtime=SteamRuntime(network)
    if delete_data and runtime.root.is_symlink():raise RuntimeError('Refusing an externally linked Steam data directory.')
    def docker(*args):
        result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=60)
        if result.returncode:raise RuntimeError('Docker cleanup failed for '+args[0]+'. Retry after checking Docker.')
        return result.stdout
    gateways=docker('ps','-aq','--filter','label='+LABEL+'='+str(os.getuid())).split()
    owned=set(gateways)|{network.name}
    for gateway in gateways:
        entry=json.loads(docker('inspect',gateway))[0]
        if entry['Config'].get('Labels',{}).get(LABEL)!=str(os.getuid()):
            raise RuntimeError('Refusing a container owned by another installation.')
        owned.add(entry['Id'])
    for worker in docker('ps','-aq','--filter','label='+WORKER_LABEL).split():
        entry=json.loads(docker('inspect',worker))[0]
        if entry['Config'].get('Labels',{}).get(WORKER_LABEL) in owned:
            docker('rm','-f',worker);progress('Removed private Steam worker.')
    for gateway in gateways:docker('rm','-f',gateway)
    if network.container:
        # Guardians see the removed container and clean their own credential files.
        network.container=None
    preserved=[]
    resources=([('volume',runtime.volume)] if delete_data else [])
    for kind,name in resources:
        inspected=subprocess.run(['docker',kind,'inspect',name],capture_output=True,text=True)
        if inspected.returncode:continue
        entry=json.loads(inspected.stdout)[0]
        labels=(entry.get('Config',{}).get('Labels') if kind=='image' else entry.get('Labels')) or {}
        legacy_volume=allow_legacy and not labels.get(OWNER_LABEL) and name==runtime.volume
        if labels.get(OWNER_LABEL)!=OWNER and not legacy_volume:
            preserved.append(name)
            progress('Preserved resource without this installation’s ownership label: '+name);continue
        docker(kind,'rm',name);progress('Removed '+kind+': '+name)
    if delete_data:
        marker=runtime.root/'environment-owner.json'
        if marker.is_file() and not marker.is_symlink():
            document=json.loads(marker.read_text())
            if document.get('owner')==OWNER:
                for name in document.get('directories',[]):
                    if name not in ('control','library'):raise RuntimeError('Invalid Steam ownership receipt.')
                    path=runtime.root/name
                    if path.is_symlink():progress('Preserved externally replaced private directory link: '+str(path))
                    elif path.exists():
                        try:shutil.rmtree(path)
                        except PermissionError:
                            # Container-created files may belong to uid 65534.
                            # Mount only this receipt-approved private directory.
                            image=json.loads(docker('image','inspect',RUNTIME_IMAGE))[0]
                            if (image.get('Config',{}).get('Labels') or {}).get(OWNER_LABEL)!=OWNER:
                                raise RuntimeError('Private data needs permission cleanup, but no owned cleanup image is available.')
                            docker('run','--rm','--network','none','--read-only','--user','0:0',
                                '--mount',f'type=bind,src={path},dst=/cleanup','--entrypoint','python3',RUNTIME_IMAGE,
                                '-B','-c',"import pathlib,shutil; root=pathlib.Path('/cleanup'); [p.unlink() if p.is_symlink() or p.is_file() else shutil.rmtree(p) for p in root.iterdir()]")
                            path.rmdir()
                marker.unlink()
                try:runtime.root.rmdir()
                except OSError:pass
                progress('Removed owned private Steam data. Exported game folders were preserved.')
        else:progress('Preserved private directory without an ownership receipt.')
    else:progress('Private Steam data and logins retained.')
    inspected=subprocess.run(['docker','image','inspect',RUNTIME_IMAGE],capture_output=True,text=True)
    if inspected.returncode==0:
        image=json.loads(inspected.stdout)[0]
        labels=image.get('Config',{}).get('Labels') or {}
        legacy_image=allow_legacy and not labels.get(OWNER_LABEL) and labels.get('io.playlite.steam.setup')
        if labels.get(OWNER_LABEL)==OWNER or legacy_image:
            docker('image','rm',RUNTIME_IMAGE);progress('Removed owned Steam image.')
        else:
            preserved.append(RUNTIME_IMAGE)
            progress('Preserved Steam image without this installation’s ownership label.')
    if preserved:raise RuntimeError('Cleanup incomplete: resources without ownership records were preserved: '+', '.join(preserved)+'. Select legacy cleanup only for resources you recognize as belonging to this profile.')
    return 'Steam environment cleanup finished.'


class EnvironmentRemovalDialog(QDialog):
    def __init__(self,network,parent=None,environment_only=False):
        super().__init__(parent)
        self.network=network;self.job=None
        self.environment_only=environment_only
        self.setWindowTitle('Uninstall Steam Downloader');self.resize(620,360)
        layout=QVBoxLayout(self)
        note=QLabel('Remove the plugin only, or also remove its owned Steam/VPN containers and Steam image. System Docker, shared dependencies and exported game folders are retained.')
        note.setWordWrap(True);layout.addWidget(note)
        self.environment=QCheckBox('Also remove the Steam environment');layout.addWidget(self.environment)
        if environment_only:
            self.setWindowTitle('Delete isolated Steam')
            note.setText('Delete the owned isolated Steam environment. The Steam Downloader plugin, exported games and shared dependencies are retained. Optionally delete private Steam data and logins below.')
            self.environment.setChecked(True)
            self.environment.hide()
        self.data=QCheckBox('Delete private Steam data and logins (including any remaining private game copies)')
        self.data.setEnabled(False);self.environment.toggled.connect(self.data.setEnabled);layout.addWidget(self.data)
        if environment_only:self.data.setEnabled(True)
        self.legacy=QCheckBox('Also delete this profile’s legacy Steam image and volume without ownership labels')
        self.legacy.setToolTip('Applies only to the profile-specific Steam image and, when private data deletion is selected, its named Steam volume. Resources labelled for another owner are retained.')
        layout.addWidget(self.legacy)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);layout.addWidget(self.log)
        controls=QHBoxLayout();self.remove=QPushButton('Uninstall');self.cancel=QPushButton('Cancel')
        self.remove.clicked.connect(self.begin);self.cancel.clicked.connect(self.reject)
        controls.addWidget(self.remove);controls.addWidget(self.cancel);layout.addLayout(controls)
        if environment_only:self.remove.setText('Delete isolated Steam')

    def begin(self):
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.network):self.log.appendPlainText('Pause or cancel downloads before uninstalling.');return
        if not self.environment.isChecked():self.accept();return
        delete_data=self.data.isChecked()
        allow_legacy=self.legacy.isChecked()
        from .plugin import Job
        success=[]
        def operation():
            result=remove_environment(self.network,delete_data,job.signals.progress.emit,allow_legacy=allow_legacy)
            success.append(True);return result
        job=Job(operation);self.job=job
        for button in (self.remove,self.cancel,self.environment,self.data,self.legacy):button.setEnabled(False)
        job.signals.progress.connect(self.log.appendPlainText)
        def finished(message):
            self.job=None;self.log.appendPlainText(message)
            if success and not self.environment_only:self.accept()
            elif success:
                self.remove.setEnabled(False)
                self.cancel.setText('Close')
                self.cancel.setEnabled(True)
            else:
                for button in (self.remove,self.cancel,self.environment,self.legacy):button.setEnabled(True)
                self.data.setEnabled(self.environment.isChecked())
        job.signals.finished.connect(finished);QThreadPool.globalInstance().start(job)

    def reject(self):
        if not self.job:super().reject()

    def closeEvent(self,event):
        if self.job:event.ignore()
        else:super().closeEvent(event)
