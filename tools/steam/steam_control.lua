-- Narrow Steam UI actions; no caller-supplied JavaScript or arbitrary URLs.
local socket = require('socket')
local json = require('json')
local cefport = require('cefport')
local peerauth = require('peerauth')
local cdpreq = require('cdpreq')
local httpresp = require('httpresp')
local request = json.decode(os.getenv('PLAYLITE_STEAM_ACTION') or '{}')
assert(type(request.appid)=='number' and request.appid%1==0 and request.appid>0 and request.appid<4294967296, 'Invalid App ID')
assert(request.action=='install' or request.action=='pause', 'Unsupported Steam action')
if request.action=='install' then
    assert(request.platform=='windows' or request.platform=='linux', 'Unsupported platform')
    assert(type(request.dlc)=='table' and #request.dlc<=256, 'Invalid DLC selection')
    for _, item in ipairs(request.dlc) do
        assert(type(item.id)=='number' and item.id%1==0 and item.id>0 and item.id<4294967296 and type(item.enabled)=='boolean', 'Invalid DLC selection')
    end
    assert(type(request.language)=='string' and request.language:match('^[a-z]+$'), 'Invalid language')
end
local port, contract = cefport.resolve(cefport.read_contract)
assert(peerauth.verify(port), 'Steam UI is not ready')
local connection = assert(socket.tcp()); connection:settimeout(3)
assert(connection:connect('127.0.0.1',port))
connection:send('GET /json HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: */*\r\n\r\n')
local raw, headers, body = '', nil, nil
repeat
    local chunk, failure, partial = connection:receive(256)
    raw=raw..(chunk or partial or '')
    assert(#raw<=131072 and (chunk or (partial and #partial>0)), 'Missing Steam targets')
    headers, body = httpresp.headers_complete(raw)
until headers
local length=assert(httpresp.content_length(headers), 'Missing target-list size')
assert(length<=131072, 'Steam target list exceeds limit')
while #body<length do
    local chunk, failure, partial=connection:receive(length-#body)
    local got=chunk or partial
    assert(got and #got>0, 'Incomplete Steam targets')
    body=body..got
end
connection:close()
local targets = json.decode(body)
local shared
for _, target in ipairs(targets) do
    if target.title=='SharedJSContext' then shared=target.webSocketDebuggerUrl;break end
end
assert(shared, 'Steam UI is not ready')
local expression = '(async()=>{const r='..json.encode(request)..[[;
let stage='platform';try{
if(r.action==='pause'){await SteamClient.Downloads.PauseAppUpdate(r.appid);return {paused:true};}
const tools=await SteamClient.Apps.GetAvailableCompatTools(r.appid);
const tool=r.platform==='windows'?'proton_experimental':'';
if(tool&&!tools.some(t=>t.strToolName===tool))throw Error('Windows compatibility is unavailable');
await SteamClient.Apps.SpecifyCompatTool(r.appid,tool);
stage='language';
await SteamClient.Apps.SetAppCurrentLanguage(r.appid,r.language);
stage='DLC';
for(const dlc of (Array.isArray(r.dlc)?r.dlc:[]))await SteamClient.Apps.SetDLCEnabled(r.appid,dlc.id,dlc.enabled);
stage='library';
const folders=await SteamClient.InstallFolder.GetInstallFolders();
const folder=folders.find(f=>f.strFolderPath==='/library'&&f.bIsMounted);
if(!folder)throw Error('The isolated download library is unavailable');
const installed=folders.some(f=>f.vecApps.some(a=>a.nAppID===r.appid));
stage='installation';
if(installed){await SteamClient.Downloads.ResumeAppUpdate(r.appid);}
else{
 await SteamClient.Installs.OpenInstallWizard([r.appid]);
 await SteamClient.Installs.SetInstallFolder(folder.nFolderIndex);
 await SteamClient.Installs.SetCreateShortcuts(false,false);
 await SteamClient.Installs.ContinueInstall();
}
return {requested:true,platform:r.platform,compatibility_tool:tool};
}catch(e){return {requested:false,failed_stage:stage};}
})()]]
local result, failure = cdpreq.evaluate(port,shared,expression,5,true)
assert(result, failure or 'Steam rejected the install request')
print('PLAYLITE_STEAM_RESULT '..json.encode(result))
