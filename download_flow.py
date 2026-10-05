"""Safe destinations and finalization for validated downloads."""
from pathlib import Path
import re
import ctypes
import os

def game_folder(root, name):
    name=re.sub(r'[\\/\x00-\x1f<>:"|?*]', '_', name).strip(' .')[:120]
    if not name: raise ValueError('Choose a game before downloading.')
    return Path(root).expanduser() / name

def prepare_destination(path):
    path=Path(path).expanduser().resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('The download folder already contains files. Choose an empty folder.')
    path.mkdir(parents=True,exist_ok=True)
    staging=path/'.playlite-download'
    staging.mkdir(mode=0o777);staging.chmod(0o777)
    return path,staging

def rename_without_overwrite(source,target):
    libc=ctypes.CDLL(None,use_errno=True)
    rename=getattr(libc,'renameat2',None)
    if rename is None:raise OSError('Atomic no-overwrite rename is unavailable.')
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(source),-100,os.fsencode(target),1):
        number=ctypes.get_errno()
        raise OSError(number,os.strerror(number),str(target))

def finalize_download(destination, staging):
    destination=Path(destination);staging=Path(staging)
    entries=list(staging.iterdir())
    if not entries: raise ValueError('No downloaded files were found.')
    for entry in entries:
        if (destination/entry.name).exists() or (destination/entry.name).is_symlink():
            raise ValueError('A destination file appeared during downloading. Files remain in staging.')
    moved=[]
    try:
        for entry in entries:
            target=destination/entry.name
            rename_without_overwrite(entry,target);moved.append(target)
    except OSError:
        for target in reversed(moved): rename_without_overwrite(target,staging/target.name)
        raise
    staging.rmdir()
