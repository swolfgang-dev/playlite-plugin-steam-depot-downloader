"""Safe destinations and finalization for validated downloads."""
from pathlib import Path
import re
import ctypes
import os
import errno
import stat
import hashlib
import shutil

def game_folder(root, name):
    name=re.sub(r'[\\/\x00-\x1f<>:"|?*]', '_', name).strip(' .')[:120]
    if not name: raise ValueError('Choose a game before downloading.')
    return Path(root).expanduser() / name

def suggested_wine_prefix(directory,root):
    name=Path(directory).name
    words=re.sub(r'([a-z0-9])([A-Z])',r'\1-\2',name)
    words=re.sub(r'([A-Z])([A-Z][a-z])',r'\1-\2',words)
    slug=re.sub(r'[^\w]+','-',words.casefold().replace('_','-')).strip('-') or 'game'
    return Path(root).expanduser()/slug


def prepare_destination(path):
    path=Path(path).expanduser().resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('The download folder already contains files. Choose an empty folder.')
    path.mkdir(parents=True,exist_ok=True)
    staging=path/'.playlite-download'
    staging.mkdir(mode=0o777);staging.chmod(0o777)
    return path,staging

def atomic_rename_without_overwrite(source,target):
    libc=ctypes.CDLL(None,use_errno=True)
    rename=getattr(libc,'renameat2',None)
    if rename is None:raise OSError(errno.ENOSYS,'Atomic no-overwrite rename is unavailable.')
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(source),-100,os.fsencode(target),1):
        number=ctypes.get_errno()
        raise OSError(number,os.strerror(number),str(target))

def rename_without_overwrite(source,target):
    try:
        atomic_rename_without_overwrite(source,target)
        return
    except OSError as error:
        if error.errno not in (errno.EINVAL,errno.ENOSYS,errno.EOPNOTSUPP,errno.EXDEV):raise
    # Some FUSE filesystems reject renameat2 flags. Never replace this with a
    # check followed by ordinary rename: another process could create the target.
    source=Path(source);target=Path(target);info=source.lstat()
    if stat.S_ISDIR(info.st_mode):
        target.mkdir(mode=0o700)  # Exclusive directory reservation.
        moved=[]
        try:
            for child in source.iterdir():
                destination=target/child.name
                rename_without_overwrite(child,destination);moved.append((child,destination))
            shutil.copystat(source,target,follow_symlinks=False)
            source.rmdir()  # A newly added source file prevents deletion.
        except Exception:
            target.chmod(0o700)
            for original,destination in reversed(moved):rename_without_overwrite(destination,original)
            try:target.rmdir()
            except OSError:pass  # Never delete a concurrently added file.
            raise
    elif stat.S_ISLNK(info.st_mode):
        os.symlink(os.readlink(source),target)
        try:
            if source.lstat().st_ino!=info.st_ino:raise OSError('Source link changed during finalization.')
            source.unlink()
        except Exception:
            target.unlink();raise
    elif stat.S_ISREG(info.st_mode):
        try:
            os.link(source,target,follow_symlinks=False)  # Atomic no-overwrite.
        except OSError as error:
            if error.errno not in (errno.EXDEV,errno.EPERM,errno.EOPNOTSUPP,errno.ENOSYS):raise
            copy_file_exclusive(source,target)
        try:
            current=source.lstat()
            if (current.st_ino,current.st_size,current.st_mtime_ns)!=(info.st_ino,info.st_size,info.st_mtime_ns):
                raise OSError('Source file changed during finalization.')
            source.unlink()
        except Exception:
            target.unlink();raise
    else:
        raise ValueError('Unsupported file type in downloaded content.')


def copy_file_exclusive(source,target,progress=None):
    """Verified copy for filesystems without hard links; retain source on failure."""
    created=False
    try:
        with os.fdopen(os.open(source,os.O_RDONLY|os.O_NOFOLLOW),'rb') as reader:
            before=os.fstat(reader.fileno());digest=hashlib.sha256()
            descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            created=True
            with os.fdopen(descriptor,'wb') as writer:
                while data:=reader.read(1024*1024):
                    digest.update(data);writer.write(data)
                    if progress:progress(len(data),'copy')
                writer.flush();os.fsync(writer.fileno())
            after=os.fstat(reader.fileno())
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
                raise OSError('Source file changed while copying.')
            copied=hashlib.sha256()
            with target.open('rb') as check:
                while data:=check.read(1024*1024):
                    copied.update(data)
                    if progress:progress(len(data),'verify')
            if copied.digest()!=digest.digest():raise OSError('Copied file failed verification.')
            shutil.copystat(source,target,follow_symlinks=False)
    except Exception:
        if created:target.unlink()
        raise


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
