"""Resolve download destinations from plugin overrides and General preferences."""
import os
from pathlib import Path
from PyQt6.QtCore import QSettings


def default_download_root():
    from .vm_backend import enabled, configuration
    if enabled():return configuration()['shared']
    override = QSettings('Playlite', 'SteamDownloader').value('download_root', '', type=str).strip()
    if override:
        return str(Path(override).expanduser())
    return general_download_root()


def general_download_root():
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'playlite'
    general = QSettings(str(data / 'ui.ini'), QSettings.Format.IniFormat)
    folder = general.value('installation/defaultFolder', '', type=str).strip()
    return str(Path(folder).expanduser()) if folder else str(Path.home() / 'Downloads')
