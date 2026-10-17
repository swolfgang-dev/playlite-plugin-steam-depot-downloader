"""Open account links with the user's regular desktop browser profile."""
from playlite.desktop import host_environment
import subprocess
from urllib.parse import urlsplit


def open_account_link(url):
    if urlsplit(url).scheme not in ('https', 'http'):
        raise ValueError('Unsupported account link.')
    environment = host_environment()
    subprocess.Popen(['xdg-open', url], env=environment, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
