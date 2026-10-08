"""Host-side credentials for resumable isolated Steam setup."""
from .desktop_links import open_account_link
from PyQt6.QtWidgets import QDialog,QFormLayout,QLineEdit,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QScrollArea,QWidget,QSizePolicy
from .credentials import VMAuthenticationWallet

class VMAuthenticationDialog(QDialog):
    def __init__(self, network, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Steam VM authentication — Playlite')
        self.resize(640,560)
        self.setMinimumSize(480,400)
        from .setup_visuals import style_dialog
        style_dialog(self)
        root=QVBoxLayout(self)
        root.setContentsMargins(24,24,24,24);root.setSpacing(20)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content=QWidget();form=QFormLayout(content)
        form.setContentsMargins(0,0,8,0);form.setVerticalSpacing(16)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        content.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(content);root.addWidget(scroll,1)
        self.values={}
        note=QLabel('Save the NordVPN token for automatic installation. Secrets are encrypted in KWallet. Enter a fresh LuaTools code at the final setup step; Hubcap is optional.')
        note.setWordWrap(True);form.addRow(note)
        for key,label in [('nord_token','NordVPN access token'),('luatools_code','LuaTools / Moon login code'),('hubcap_key','Hubcap Manifest API key')]:
            field=QLineEdit();field.setEchoMode(QLineEdit.EchoMode.Password);field.setMaxLength(2048)
            self.values[key]=field;form.addRow(label,field)
        links=QLabel('<a href="https://my.nordaccount.com/">Nord Account: get an access token</a> · <a href="https://discord.gg/luatools">LuaTools Discord: /login</a>')
        links.setWordWrap(True);links.setOpenExternalLinks(False); links.linkActivated.connect(open_account_link);form.addRow(links)
        steam=QLabel('Steam: sign in once using the VM desktop, including Steam Guard approval. Steam keeps its own session; Playlite does not store your Steam password. This step becomes available after Steam installs.')
        steam.setWordWrap(True);steam.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum);form.addRow(steam)
        button=QPushButton('Open Steam sign-in…');button.setEnabled((network.profile/'vm.json').exists());button.clicked.connect(network.open_desktop);form.addRow(button)
        self.status=QLabel('');self.status.setWordWrap(True);root.addWidget(self.status)
        try:
            saved=VMAuthenticationWallet().read() or {}
            for key,field in self.values.items():field.setText(saved.get(key,''))
        except (RuntimeError,ValueError):pass
        footer=QHBoxLayout();footer.setSpacing(12);footer.addStretch()
        close=QPushButton('Cancel');close.clicked.connect(self.reject);footer.addWidget(close)
        save=QPushButton('Save credentials');save.setDefault(True);save.clicked.connect(self.save);footer.addWidget(save)
        root.addLayout(footer)
    def save(self):
        values={key:field.text().strip() for key,field in self.values.items()}
        if not values['nord_token']:
            self.status.setText('Enter a NordVPN access token.');return
        if any(any(c in value for c in '\r\n\0') for value in values.values()):
            self.status.setText('Credentials must not contain line breaks.');return
        try:VMAuthenticationWallet().save(values)
        except (RuntimeError,ValueError) as error:
            self.status.setText(str(error));return
        for field in self.values.values():field.clear()
        self.accept()
