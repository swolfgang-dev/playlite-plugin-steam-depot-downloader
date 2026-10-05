"""Use KDE's encrypted wallet; never persist credentials in plugin settings."""
import json
from PyQt6.QtCore import QVariant, QMetaType
from PyQt6.QtDBus import QDBusConnection, QDBusInterface

APP = 'Playlite Steam Depot Downloader'
FOLDER = 'Playlite Steam Downloader'
ENTRY = 'NordVPN service credentials'


def validate_credentials(username, password):
    if not username.strip() or not password:
        raise ValueError('Enter your NordVPN service username and password.')
    if any(character in username + password for character in '\r\n\0'):
        raise ValueError('Service credentials must not contain newlines.')
    return username.strip(), password


class Wallet:
    def __init__(self):
        bus = QDBusConnection.sessionBus()
        self.interface = None
        for service in ('org.kde.kwalletd6', 'org.kde.kwalletd5'):
            interface = QDBusInterface(service, '/modules/kwalletd' + service[-1], 'org.kde.KWallet', bus)
            if interface.isValid():
                self.interface = interface
                break
        if self.interface is None:
            raise RuntimeError('KWallet is unavailable. Enable the KDE wallet to save VPN credentials. Credentials will not be saved as plaintext.')

    def call(self, method, *arguments):
        reply = self.interface.call(method, *arguments)
        if reply.errorName():
            raise RuntimeError('The desktop wallet request failed or was cancelled.')
        return reply.arguments()[0] if reply.arguments() else None

    def opened(self):
        name = self.call('networkWallet')
        window = QVariant(0)
        window.convert(QMetaType(QMetaType.Type.LongLong.value))
        handle = self.call('open', name, window, APP)
        if handle is None or handle < 0:
            raise RuntimeError('Unlock the desktop wallet to access VPN credentials.')
        return handle

    def read(self):
        handle = self.opened()
        try:
            value = self.call('readPassword', handle, FOLDER, ENTRY, APP)
            if not value:
                raise ValueError('Save your NordVPN service credentials first.')
            data = json.loads(value)
            return validate_credentials(data['username'], data['password'])
        finally:
            self.call('close', handle, False, APP)

    def save(self, username, password):
        username, password = validate_credentials(username, password)
        handle = self.opened()
        try:
            if not self.call('hasFolder', handle, FOLDER, APP):
                if not self.call('createFolder', handle, FOLDER, APP):
                    raise RuntimeError('Could not create the wallet folder.')
            if self.call('writePassword', handle, FOLDER, ENTRY,
                         json.dumps({'username': username, 'password': password}), APP) != 0:
                raise RuntimeError('Could not save VPN credentials.')
        finally:
            self.call('close', handle, False, APP)

    def clear(self):
        """Remove only this plugin's saved NordVPN service credentials."""
        handle = self.opened()
        try:
            if self.call('hasEntry', handle, FOLDER, ENTRY, APP):
                if self.call('removeEntry', handle, FOLDER, ENTRY, APP) != 0:
                    raise RuntimeError('Could not remove VPN credentials from KWallet.')
        finally:
            self.call('close', handle, False, APP)


class MoonSessionWallet:
    """Keep Moon tokens in a separate encrypted entry from NordVPN credentials."""
    entry = 'Moon session'

    def read(self):
        wallet = Wallet(); handle = wallet.opened()
        try:
            value = wallet.call('readPassword', handle, FOLDER, self.entry, APP)
            return json.loads(value) if value else None
        finally:
            wallet.call('close', handle, False, APP)

    def save(self, session):
        wallet = Wallet(); handle = wallet.opened()
        try:
            if not wallet.call('hasFolder', handle, FOLDER, APP):
                if not wallet.call('createFolder', handle, FOLDER, APP):
                    raise RuntimeError('Could not create the wallet folder.')
            if wallet.call('writePassword', handle, FOLDER, self.entry, json.dumps(session), APP) != 0:
                raise RuntimeError('Could not save the Moon session in KWallet.')
        finally:
            wallet.call('close', handle, False, APP)

    def clear(self):
        wallet = Wallet(); handle = wallet.opened()
        try:
            if wallet.call('hasEntry', handle, FOLDER, self.entry, APP):
                if wallet.call('removeEntry', handle, FOLDER, self.entry, APP) != 0:
                    raise RuntimeError('Could not remove the Moon session from KWallet.')
        finally:
            wallet.call('close', handle, False, APP)


class SteamSessionWallet(MoonSessionWallet):
    def __init__(self, username):
        import hashlib
        self.entry = 'Steam session ' + hashlib.sha256(username.strip().lower().encode()).hexdigest()


class HubcapKeyWallet(MoonSessionWallet):
    entry = 'Hubcap API key'
