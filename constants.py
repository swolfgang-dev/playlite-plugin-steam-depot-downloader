IMAGE = 'qmcgaw/gluetun@sha256:2733bb22b27e3efa7a9f2cef9057ec12791b8b225793fcd3dbfd0508404dfc25'
import os
PROFILE_SUFFIX = '-repo' if os.environ.get('PLAYLITE_PROFILE') == 'repo' else ''
LABEL = 'io.playlite.steam-downloader' + PROFILE_SUFFIX
WORKER_LABEL = LABEL + '.gateway'
OWNER_LABEL = 'io.playlite.steam.owner'
OWNER = str(os.getuid()) + PROFILE_SUFFIX
