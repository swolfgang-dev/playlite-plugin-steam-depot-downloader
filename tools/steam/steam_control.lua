-- Narrow Steam UI actions; no caller-supplied JavaScript or arbitrary URLs.
local socket = require('socket')
local json = require('json')
local cefport = require('cefport')
local peerauth = require('peerauth')
local cdpreq = require('cdpreq')
local httpresp = require('httpresp')
local request = json.decode(os.getenv('PLAYLITE_STEAM_ACTION') or '{}')
assert(type(request.appid)=='number' and request.appid%1==0 and request.appid>0 and request.appid<4294967296, 'Invalid App ID')
assert(request.action=='install' or request.action=='pause' or request.action=='eula_status' or request.action=='eula_accept' or request.action=='package_info' or request.action=='installed_games' or request.action=='uninstall' or request.action=='download_progress', 'Unsupported Steam action')
if request.action=='install' then
    assert(type(request.restart_paused)=='boolean', 'Invalid retry selection')
    assert(request.platform=='windows' or request.platform=='linux', 'Unsupported platform')
    assert(type(request.dlc)=='table' and #request.dlc<=256, 'Invalid DLC selection')
    for _, item in ipairs(request.dlc) do
        assert(type(item.id)=='number' and item.id%1==0 and item.id>0 and item.id<4294967296 and type(item.enabled)=='boolean', 'Invalid DLC selection')
    end
    assert(type(request.language)=='string' and request.language:match('^[a-z]+$'), 'Invalid language')
end
if request.action=='package_info' then
    assert(type(request.package_ids)=='table' and #request.package_ids>0 and #request.package_ids<=64, 'Invalid packages')
    for _, id in ipairs(request.package_ids) do assert(type(id)=='number' and id%1==0 and id>0 and id<4294967296, 'Invalid package ID') end
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
if(r.action==='download_progress'){
 let overview=null;
 const sub=SteamClient.Downloads.RegisterForDownloadOverview(o=>{
  if(o.remote_client_id==='0'&&o.update_appid===r.appid){
   const transfer=o.progress&&o.progress[2];
   const disk=o.progress&&o.progress[3];
   overview={downloaded:transfer?transfer.bytes_in_progress:0,total:transfer?transfer.bytes_total:0,disk_processed:disk?disk.bytes_in_progress:0,disk_total:disk?disk.bytes_total:0,speed:o.update_network_bytes_per_second,state:o.update_state,paused:o.paused};
  }
 });
 try{await new Promise(resolve=>setTimeout(resolve,500));}finally{sub.unregister();}
 return {transfer:overview};
}
if(r.action==='installed_games'){
 const folders=await SteamClient.InstallFolder.GetInstallFolders();
 return {folders:folders.map(f=>({path:f.strFolderPath,mounted:f.bIsMounted,apps:f.vecApps.map(a=>({appid:a.nAppID,name:a.strAppName,size:a.nUsedSize,staged:a.nStagedSize}))}))};
}
if(r.action==='uninstall'){
 const folders=await SteamClient.InstallFolder.GetInstallFolders();
 if(!folders.some(f=>f.bIsMounted&&f.vecApps.some(a=>a.nAppID===r.appid)))return {requested:false,error:'Game is no longer installed in isolated Steam.'};
 let error=null;
 const sub=SteamClient.Installs.RegisterForShowFailedUninstall((appid,code)=>{if(appid===r.appid)error='Steam uninstall failed (code '+code+').';});
 try{
  await SteamClient.Installs.OpenUninstallWizard([r.appid],true);
  await new Promise(resolve=>setTimeout(resolve,500));
 }finally{sub.unregister();}
 return {requested:!error,error};
}
if(r.action==='package_info'){
 let text='';
 const sub=SteamClient.Console.RegisterForSpewOutput(entry=>{if(entry.spew_type==='info'&&typeof entry.spew==='string'&&text.length<524288)text+=entry.spew;});
 try{
   await SteamClient.Console.ExecCommand('app_info_print '+r.appid);
   for(const id of r.package_ids)await SteamClient.Console.ExecCommand('package_info_print '+id);
   await new Promise(resolve=>setTimeout(resolve,1000));
 }finally{if(sub&&sub.unregister)sub.unregister();}
 return {text};
}
if(r.action==='eula_status'){return {agreements:await SteamClient.Apps.LoadEula(r.appid)};}
if(r.action==='eula_accept'){
 const agreements=await SteamClient.Apps.LoadEula(r.appid);
 const agreement=agreements.find(e=>e.id===r.eula_id&&e.version===r.eula_version);
 if(!agreement)return {accepted:false};
 await SteamClient.Apps.MarkEulaAccepted(r.appid,agreement.id,agreement.version);
 await SteamClient.Installs.ContinueInstall();
 return {accepted:true};
}
if(r.action==='pause'){await SteamClient.Downloads.PauseAppUpdate(r.appid,'0');return {paused:true};}
if(r.restart_paused){
 stage='cancel paused download';
 await SteamClient.Downloads.RemoveFromDownloadList(r.appid,'0');
}
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
if(installed){
 if(r.restart_paused)await SteamClient.Downloads.QueueAppUpdate(r.appid,'0');
 if(r.restart_paused)await SteamClient.Downloads.EnableAllDownloads(true,'0');
 await SteamClient.Downloads.ResumeAppUpdate(r.appid,'0');
}
else{
 await SteamClient.Installs.OpenInstallWizard([r.appid]);
 await SteamClient.Installs.SetInstallFolder(folder.nFolderIndex);
 await SteamClient.Installs.SetCreateShortcuts(false,false);
 await SteamClient.Installs.ContinueInstall();
}
return {requested:true,platform:r.platform,compatibility_tool:tool,restarted_paused:r.restart_paused};
}catch(e){return {requested:false,failed_stage:stage};}
})()]]
local result, failure = cdpreq.evaluate(port,shared,expression,5,true)
assert(result, failure or 'Steam rejected the install request')
print('PLAYLITE_STEAM_RESULT '..json.encode(result))
