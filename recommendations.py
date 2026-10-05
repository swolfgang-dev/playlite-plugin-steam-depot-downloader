"""Public VPN bootstrap metadata; no credentials or download traffic."""
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def api(path, parameters=None):
    url = 'https://api.nordvpn.com/v1/' + path
    if parameters: url += '?' + urlencode(parameters)
    request = Request(url, headers={'User-Agent':'Playlite-Steam-Depot-Downloader/0.1'})
    with urlopen(request, timeout=12) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024: raise ValueError('NordVPN recommendation response is too large.')
    return json.loads(body)


def recommended_servers(preferences):
    preferences.validate()
    parameters = {'limit':20, 'filters[servers_technologies][identifier]':'openvpn_' + preferences.protocol}
    if preferences.country:
        countries = api('servers/countries')
        if not isinstance(countries, list): raise ValueError('NordVPN returned invalid country metadata.')
        country = next((row for row in countries if isinstance(row, dict) and str(row.get('name', '')).casefold() == preferences.country.casefold()), None)
        if not country or type(country.get('id')) is not int:
            raise ValueError('The selected country was not found in NordVPN recommendations.')
        parameters['filters[country_id]'] = country['id']
    rows = api('servers/recommendations', parameters)
    if not isinstance(rows, list): raise ValueError('NordVPN returned invalid recommendations.')
    result = []
    for row in rows:
        if not isinstance(row, dict): continue
        hostname = row.get('hostname', '')
        if row.get('status') == 'online' and re.fullmatch(r'[a-z]{2}\d+\.nordvpn\.com', hostname) and hostname not in result:
            result.append(hostname)
    if not result: raise ValueError('NordVPN returned no available servers.')
    return result
