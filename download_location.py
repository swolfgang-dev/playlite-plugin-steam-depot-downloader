"""Resolve download destinations from plugin overrides and General preferences."""
import os
from pathlib import Path
from PyQt6.QtCore import QSettings


def default_download_root():
    override = QSettings('Playlite', 'SteamDownloader').value('download_root', '', type=str).strip()
    if override:
        return str(Path(override).expanduser())
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'playlite'
    general = QSettings(str(data / 'ui.ini'), QSettings.Format.IniFormat)
    folder = general.value('installation/defaultFolder', '', type=str).strip()
    return str(Path(folder).expanduser()) if folder else str(Path.home() / 'Downloads')
