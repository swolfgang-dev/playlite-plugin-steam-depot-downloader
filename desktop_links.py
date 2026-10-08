"""Open account links with the user's regular desktop browser profile."""
import os
import pwd
import subprocess
from urllib.parse import urlsplit


def open_account_link(url):
    if urlsplit(url).scheme not in ('https', 'http'):
        raise ValueError('Unsupported account link.')
    environment = os.environ.copy()
    if environment.get('PLAYLITE_PROFILE') == 'repo':
        environment['HOME'] = environment.get('PLAYLITE_HOST_HOME') or pwd.getpwuid(os.getuid()).pw_dir
        for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
            value = environment.get('PLAYLITE_HOST_' + key)
            if value:
                environment[key] = value
            else:
                environment.pop(key, None)
    subprocess.Popen(['xdg-open', url], env=environment, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
