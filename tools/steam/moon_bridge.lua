-- Playlite's narrow control interface, executed only by LuaMoon's own backend.
local playlite_previous_tick = lifecycle.on_tick
lifecycle.on_tick = function(...)
    if playlite_previous_tick then playlite_previous_tick(...) end
    local file = io.open('/control/moon-request.json', 'rb')
    if not file then return end
    local raw = file:read(8193); file:close()
    os.remove('/control/moon-request.json')
    if #raw > 8192 then return end
    local ok, request = pcall(cjson.decode, raw)
    if not ok or type(request) ~= 'table' then return end
    local appid = tonumber(request.appid)
    if not appid or appid % 1 ~= 0 or appid <= 0 or appid >= 4294967296 then return end
    local methods = {
        add = StartAddViaLuaToolsSmart,
        add_status = GetAddViaLuaToolsStatus,
        cancel_add = CancelAddViaLuaTools,
    }
    local method = methods[request.command]
    if not method then return end
    local success, result = pcall(method, {appid = appid})
    local response = cjson.encode({id = request.id, ok = success, result = success and result or 'LuaMoon request failed'})
    local output = io.open('/control/moon-response.tmp', 'wb')
    if output then
        output:write(response); output:close()
        os.rename('/control/moon-response.tmp', '/control/moon-response.json')
    end
end
return lifecycle
