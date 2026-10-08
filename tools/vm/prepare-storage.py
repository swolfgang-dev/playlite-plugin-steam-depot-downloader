#!/usr/bin/env python3
"""Keep Steam metadata private and content on the exported merged filesystem."""
import os
import json
from pathlib import Path


def prepare():
    shared = Path('/mnt/standalone')
    if not os.path.ismount(shared):
        raise RuntimeError('Shared games folder is not mounted.')
    config = json.loads(Path('/etc/playlite-vm.json').read_text())
    staging = shared / config['staging']
    apps = Path.home() / '.steam/debian-installation/steamapps'
    apps.mkdir(parents=True, exist_ok=True)
    for relative, destination in [('common', shared),
                                   ('downloading', staging / 'downloading'),
                                   ('temp', staging / 'temp'),
                                   ('workshop/content', shared / 'Workshop')]:
        destination.mkdir(parents=True, exist_ok=True)
        link = apps / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.resolve() != destination.resolve():
                raise RuntimeError('Unexpected Steam storage link: ' + str(link))
        elif link.exists():
            # Never replace existing files or a populated Workshop download.
            if not link.is_dir() or any(link.iterdir()):
                raise RuntimeError('Existing Steam content requires review: ' + str(link))
            link.rmdir()
            link.symlink_to(destination, target_is_directory=True)
        else:
            link.symlink_to(destination, target_is_directory=True)


if __name__ == '__main__':
    prepare()
