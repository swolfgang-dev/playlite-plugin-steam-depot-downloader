"""Bounded Steam store search over the plugin's isolated transport."""
import json
from urllib.parse import urlencode

RESULT_LIMIT = 8
MAX_COVER_BYTES = 2 * 1024 * 1024

class GameSearch:
    def __init__(self, transport):
        self.transport = transport
        self.cache = {}

    def search(self, query):
        query = query.strip()
        if len(query) < 3 or query.isdecimal():
            return []
        response = json.loads(self.transport.request('https://store.steampowered.com/api/storesearch/?' + urlencode({'term':query, 'l':'english', 'cc':'CA'})))
        if not isinstance(response, dict) or not isinstance(response.get('items'), list):
            raise ValueError('Steam returned an unexpected search response.')
        rows = []; seen = set()
        for item in response['items']:
            if not isinstance(item, dict): continue
            app = item.get('id'); name = item.get('name')
            if type(app) != int or not 0 < app < 2**32 or not isinstance(name, str) or not name.strip() or app in seen: continue
            seen.add(app); rows.append({'id':app, 'name':name})
            if len(rows) == RESULT_LIMIT: break
        return rows

    def details(self, app):
        response = json.loads(self.transport.request('https://store.steampowered.com/api/appdetails?' + urlencode({'appids':app,'l':'english','cc':'CA'})))
        entry = response.get(str(app), {})
        if not entry.get('success') or not isinstance(entry.get('data', {}).get('name'), str):
            raise ValueError('Steam could not find that App ID.')
        return {'id':app,'name':entry['data']['name']}

    def cover(self, app):
        if app in self.cache: return self.cache[app]
        if type(app) != int or not 0 < app < 2**32: raise ValueError('Invalid Steam App ID.')
        try:
            data = self.transport.request(f'https://cdn.cloudflare.steamstatic.com/steam/apps/{app}/library_600x900.jpg')
            if len(data) > MAX_COVER_BYTES: data = b''
        except Exception:
            data = b''  # A missing cover must not prevent selection.
        if len(self.cache) >= 32: self.cache.pop(next(iter(self.cache)))
        self.cache[app] = data
        return data
