"""Standalone cleanup owned by Steam Downloader; invoked before plugin removal."""
import os
import json
import shutil
from pathlib import Path
LABEL = 'io.playlite.steam-downloader'
OWNER_LABEL = 'io.playlite.steam.owner'


def cleanup(data, config, args, remove, run, docker_action, failures):
    def owned_resource(kind,name):
        response=run(['docker',kind,'inspect',name])
        if response.returncode:return False
        entry=json.loads(response.stdout)[0]
        labels=(entry.get('Config',{}).get('Labels') if kind=='image' else entry.get('Labels')) or {}
        if labels.get(OWNER_LABEL)!=str(os.getuid()):
            print('Retain resource without ownership label:',name);return False
        return True
    gateway=f'playlite-steam-downloader-vpn-{os.getuid()}'
    volume=f'playlite-steam-home-{os.getuid()}'
    if shutil.which('docker'):
        response=run(['docker','ps','-aq','--filter','label='+LABEL+'='+str(os.getuid())])
        # The gateway carries the user label; workers carry its separate gateway label.
        workers=run(['docker','ps','-aq','--filter','label='+LABEL+'.gateway'])
        if response.returncode or workers.returncode:
            failures.append('Docker is unavailable; isolated container cleanup was not completed.')
        else:
            owned_gateways={gateway}
            for identifier in response.stdout.split():
                inspected=run(['docker','inspect',identifier])
                if inspected.returncode:continue
                entry=json.loads(inspected.stdout)[0]
                if (entry['Config'].get('Labels') or {}).get(LABEL)==str(os.getuid()):
                    owned_gateways.add(identifier)
                    owned_gateways.add(entry.get('Id',identifier))
            for identifier in dict.fromkeys(workers.stdout.split()+response.stdout.split()):
                inspected=run(['docker','inspect',identifier])
                if inspected.returncode:failures.append('Could not inspect container '+identifier);continue
                entry=json.loads(inspected.stdout)[0];labels=entry['Config'].get('Labels') or {}
                owned=(labels.get(LABEL)==str(os.getuid()) or labels.get(LABEL+'.gateway') in owned_gateways)
                if not owned:
                    if identifier in response.stdout.split():failures.append('Refusing unowned container '+identifier)
                    else:print('Retain another installation’s worker:',identifier)
                    continue
                docker_action(['rm','-f',identifier])
        if args.purge_data and owned_resource('volume',volume):
            docker_action(['volume','rm',volume])
    else:print('Docker not installed; no container cleanup performed.')
    if args.purge_data:
        root=data/'steam-runtime'
        marker=root/'environment-owner.json'
        if root.is_symlink():
            print('Retain external Steam directory link:',root)
        elif marker.is_file() and not marker.is_symlink():
            document=json.loads(marker.read_text())
            names=document.get('directories',[])
            if document.get('owner')!=str(os.getuid()) or any(name not in ('control','library') for name in names):
                failures.append('Invalid Steam data ownership receipt.')
            else:
                for name in names:
                    path=root/name
                    if args.dry_run:
                        print('Remove owned private Steam directory:',path)
                    elif path.is_symlink():
                        print('Retain externally replaced Steam directory link:',path)
                    elif path.exists():
                        try:shutil.rmtree(path)
                        except PermissionError:
                            if shutil.which('docker') and owned_resource('image','playlite-steam-runtime:test'):
                                docker_action(['run','--rm','--network','none','--read-only','--user','0:0',
                                    '--mount',f'type=bind,src={path},dst=/cleanup','--entrypoint','python3','playlite-steam-runtime:test',
                                    '-B','-c',"import pathlib,shutil; root=pathlib.Path('/cleanup'); [p.unlink() if p.is_symlink() or p.is_file() else shutil.rmtree(p) for p in root.iterdir()]"])
                                try:path.rmdir()
                                except OSError as error:failures.append(str(error))
                            else:failures.append('Private Steam data needs permission cleanup: '+str(path))
                remove(marker)
                if not args.dry_run:
                    try:root.rmdir()
                    except OSError:pass
        elif root.exists():print('Retain private Steam data without ownership receipt:',root)
    if args.remove_resource_images and shutil.which('docker') and owned_resource('image','playlite-steam-runtime:test'):
        docker_action(['image','rm','playlite-steam-runtime:test'])
    if args.purge_secrets:
        print('Remove KWallet folder: Playlite Steam Downloader')
        if not args.dry_run:
            code='''from PyQt6.QtCore import QCoreApplication,QVariant,QMetaType
from PyQt6.QtDBus import QDBusConnection,QDBusInterface
app=QCoreApplication([])
for name in ('org.kde.kwalletd6','org.kde.kwalletd5'):
 interface=QDBusInterface(name,'/modules/kwalletd'+name[-1],'org.kde.KWallet',QDBusConnection.sessionBus())
 if interface.isValid():break
else:raise SystemExit('KWallet is unavailable; credentials were not removed.')
def call(method,*args):
 reply=interface.call(method,*args)
 if reply.errorName():raise SystemExit('KWallet request failed.')
 return reply.arguments()[0] if reply.arguments() else None
window=QVariant(0);window.convert(QMetaType(QMetaType.Type.LongLong.value))
handle=call('open',call('networkWallet'),window,'Playlite Uninstaller')
if handle is None or handle<0:raise SystemExit('KWallet unlock cancelled; credentials were not removed.')
try:
 if call('hasFolder',handle,'Playlite Steam Downloader','Playlite Uninstaller'):
  if not call('removeFolder',handle,'Playlite Steam Downloader','Playlite Uninstaller'):raise SystemExit('Could not remove wallet folder.')
finally:call('close',handle,False,'Playlite Uninstaller')
'''
            python=data/'runtime/bin/python'
            if not python.exists():python=Path('/usr/bin/python3')
            response=run([str(python),'-c',code])
            if response.returncode:failures.append('KWallet cleanup failed: '+response.stderr.strip())
