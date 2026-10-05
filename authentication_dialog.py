"""Small account-specific login dialogs; secrets are kept in KWallet."""
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QLabel, QPushButton
from .credentials import Wallet, MoonSessionWallet, HubcapKeyWallet
from .moon import Moon
from .providers import Transport

class CredentialDialog(QDialog):
    def __init__(self, provider, network, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.network = network
        self.jobs = set()
        self.busy = False
        self.setWindowTitle(f'{provider} login — Playlite')
        self.resize(540, 240)
        form = QFormLayout(self)
        self.username = QLineEdit()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        if provider == 'NordVPN':
            form.addRow('Service username', self.username)
            form.addRow('Service password', self.secret)
            info = QLabel('<a href="https://support.nordvpn.com/hc/en-us/articles/19685514639633">Find NordVPN service credentials</a>. Use service credentials, rather than your account password.')
        elif provider == 'Moon':
            self.secret.setMaxLength(6)
            form.addRow('Login code', self.secret)
            info = QLabel('<a href="https://discord.gg/luatools">Open LuaTools Discord</a>. Run /login and paste the six-character code. Connect NordVPN first.')
        else:
            form.addRow('API key', self.secret)
            info = QLabel('Your API key is saved in KWallet. Its acceptance is confirmed when you fetch a manifest pack.')
        info.setOpenExternalLinks(True); info.setWordWrap(True); form.addRow(info)
        self.status = QLabel(''); self.status.setWordWrap(True); form.addRow(self.status)
        self.submit = QPushButton('Authenticate')
        self.submit.clicked.connect(self.save); self.secret.returnPressed.connect(self.save)
        form.addRow(self.submit)
        self.logout = QPushButton('Forget credentials' if provider == 'NordVPN' else 'Sign out' if provider == 'Moon' else 'Remove saved API key')
        self.logout.clicked.connect(self.remove); form.addRow(self.logout)
        self.close_button = QPushButton('Close'); self.close_button.clicked.connect(self.close); form.addRow(self.close_button)

    def run(self, operation):
        if self.busy: return
        from .plugin import Job
        self.busy = True; self.submit.setEnabled(False); self.close_button.setEnabled(False)
        if hasattr(self, 'logout'): self.logout.setEnabled(False)
        job = Job(operation); self.jobs.add(job)
        def done(message):
            self.jobs.discard(job); self.busy = False
            self.status.setText(message); self.submit.setEnabled(True); self.close_button.setEnabled(True)
            if hasattr(self, 'logout'): self.logout.setEnabled(True)
        job.signals.finished.connect(done)
        self.status.setText('Signing in through the isolated VPN…' if self.provider == 'Moon' else 'Updating KWallet…')
        QThreadPool.globalInstance().start(job)

    def save(self):
        secret = self.secret.text(); username = self.username.text(); self.secret.clear()
        def operation():
            if self.provider == 'Moon':
                Moon(Transport(self.network), MoonSessionWallet()).login(secret)
                self.network.moon_confirmed = True
                return 'Moon login confirmed. Session saved in KWallet.'
            if self.provider == 'NordVPN':
                Wallet().save(username, secret)
                return 'Service credentials saved. Choose Connect to confirm the VPN connection.'
            if not secret.strip() or any(c in secret for c in '\r\n\0'):
                raise ValueError('Enter a valid Hubcap API key.')
            HubcapKeyWallet().save({'key':secret.strip()})
            self.network.hubcap_confirmed = False
            return 'API key saved. Fetch a manifest pack to confirm acceptance.'
        self.run(operation)

    def remove(self):
        def operation():
            if self.provider == 'NordVPN':
                Wallet().clear()
                return 'Saved VPN credentials removed. The current connection is unchanged.'
            (MoonSessionWallet() if self.provider == 'Moon' else HubcapKeyWallet()).clear()
            setattr(self.network, self.provider.lower() + '_confirmed', False)
            return 'Saved authentication removed.'
        self.run(operation)

    def closeEvent(self, event):
        if self.busy: event.ignore(); return
        self.secret.clear()
        super().closeEvent(event)
