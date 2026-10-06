"""Store presentation metadata; the isolated Steam client selects installation content."""
import json
from concurrent.futures import ThreadPoolExecutor


def fetch_catalog(transport,appid,include_dlc=True):
    def details(identifier):
        try:
            response=json.loads(transport.request(f'https://store.steampowered.com/api/appdetails?appids={identifier}&l=english'))
            entry=response.get(str(identifier),{})
            return entry.get('data',{}) if entry.get('success') else {}
        except (ValueError,RuntimeError):return {}
    game=details(appid)
    if not game:raise RuntimeError('Steam store information is unavailable for this game. Retry the search.')
    ids=game.get('dlc',[]) if include_dlc else []
    if not isinstance(ids,list):ids=[]
    ids=list(dict.fromkeys(value for value in ids if type(value) is int and 0<value<2**32))[:256]
    with ThreadPoolExecutor(max_workers=4) as pool:
        dlc=[{'id':identifier,'name':data.get('name',''),'depots':{}} for identifier,data in zip(ids,pool.map(details,ids))]
    platforms=game.get('platforms',{})
    return {'game':{'id':appid,'name':game.get('name',''),'depots':{}},'dlc':dlc,
            '_steam_catalog':True,'platforms':[name for name in ('windows','linux') if platforms.get(name)]}
