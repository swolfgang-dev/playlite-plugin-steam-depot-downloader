"""Timestamped, bounded downloader diagnostics without authentication secrets."""
from datetime import datetime
from pathlib import Path
import os
import re
import time
from PyQt6.QtWidgets import QPlainTextEdit,QLabel


def redact(message):
    message=re.sub(r'(?i)(Bearer\s+)[^\s,;]+',r'\1[redacted]',message)
    message=re.sub(r'(?i)((?:password|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization)["\s]*[:=]\s*["\s]*)[^\s,";]+',r'\1[redacted]',message)
    return re.sub(r'(?i)([?&](?:token|key|code|auth)=[^&\s]*)',lambda match:match.group(0).split('=')[0]+'=[redacted]',message)


class DownloaderLog(QPlainTextEdit):
    def __init__(self,parent=None,path=None):
        super().__init__(parent)
        self.path=Path(path or Path.home()/'.local/share/playlite/logs/steam-downloader.log')
        self.last='';self.last_connection=0
        self.setReadOnly(True);self.document().setMaximumBlockCount(2000)

    def appendPlainText(self,message):
        message=redact(str(message).strip())
        if not message or message==self.last:return
        if message.startswith('Connecting to NordVPN ('):
            now=time.monotonic()
            if now-self.last_connection<10:return
            self.last_connection=now
        self.last=message
        line=datetime.now().astimezone().strftime('[%Y-%m-%d %H:%M:%S %z] ')+message
        super().appendPlainText(line)
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if self.path.exists() and self.path.stat().st_size>1024*1024:
                self.path.replace(self.path.with_suffix('.log.previous'))
            descriptor=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(descriptor,'a') as stream:stream.write(line+'\n')
        except OSError:pass  # Logging must not interrupt a download.


class LoggedStatus(QLabel):
    """Keep compatibility with status consumers while displaying messages in the log."""
    def __init__(self,message,log):
        super().__init__(message)
        self.log=log

    def setText(self,message):
        super().setText(message)
        self.log.appendPlainText(message)
