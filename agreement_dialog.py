"""Explicit user review and acceptance of a Steam installation agreement."""
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPlainTextEdit,QPushButton

class AgreementDialog(QDialog):
    def __init__(self,game,agreement,parent=None):
        super().__init__(parent)
        self.setWindowTitle('Steam agreement — Playlite');self.resize(760,620)
        layout=QVBoxLayout(self)
        label=QLabel(f'{game}\nAgreement: {agreement["id"]} · Version: {agreement["version"]}')
        label.setWordWrap(True);layout.addWidget(label)
        text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(agreement['text']);layout.addWidget(text)
        note=QLabel('Click Accept to agree to these terms and continue this Steam installation.')
        note.setWordWrap(True);layout.addWidget(note)
        buttons=QHBoxLayout();buttons.setSpacing(10);buttons.addStretch()
        decline=QPushButton('Cancel download');decline.clicked.connect(self.reject)
        accept=QPushButton('Accept agreement');accept.clicked.connect(self.accept)
        buttons.addWidget(decline);buttons.addWidget(accept);layout.addLayout(buttons)
        decline.setDefault(True);accept.setAutoDefault(False)
