"""Bounded Workshop control, run as the existing VM desktop user."""
import json
import os
from pathlib import Path
import sys
import bridge
from guest_control import vpn_ready, launch

HOME = Path.home()
bridge.HOME = HOME
bridge.CONTROL = HOME / '.local/state/playlite-vm/control'
bridge.CONTROL.mkdir(parents=True, exist_ok=True, mode=0o700)


def dispatch(request):
    command = request.get('command')
    if command == 'status':
        result = bridge.dispatch(request)
        result['vpn_connected'] = False
        try:
            vpn_ready()
            result['vpn_connected'] = True
        except RuntimeError:
            pass
        return result
    if command == 'start':
        vpn_ready(connect=True)
        if not bridge.steam_running():
            launch([bridge.steam_binary(), '-cef-disable-gpu', '-cef-enable-debugging'])
        return {'started': True}
    if command in ('add', 'add_status', 'cancel_add', 'authentication_status', 'has_game'):
        vpn_ready()
        return bridge.dispatch(request)
    if command == 'workshop':
        vpn_ready()
        app = bridge.app_id(request.get('appid'))
        item = request.get('item')
        if item is not None and (type(item) is not int or not 0 < item < 2**64):
            raise ValueError('Invalid Workshop item ID.')
        uri = ('steam://url/CommunityFilePage/' + str(item) if item else
               'steam://url/SteamWorkshopPage/' + str(app))
        launch([bridge.steam_binary(), uri])
        return {'opened': True}
    raise ValueError('Unsupported VM command.')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        request = json.loads(sys.stdin.read(8193))
        if not isinstance(request, dict):
            raise ValueError('Invalid request.')
        print(json.dumps({'ok': True, 'result': dispatch(request)}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        sys.exit(1)
