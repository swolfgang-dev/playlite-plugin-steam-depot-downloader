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
    local function authentication_status()
        local function decoded(fn, ...)
            local ok, value = pcall(fn, ...)
            if not ok then return {} end
            if type(value) == 'string' then
                local parsed, data = pcall(cjson.decode, value)
                return parsed and type(data) == 'table' and data or {}
            end
            return type(value) == 'table' and value or {}
        end
        local auth = decoded(GetLuaToolsAuthStatus)
        local result = {luatools = {state = auth.configured == true and 'saved_session' or auth.success == true and 'not_signed_in' or 'unavailable'}, hubcap = {state = 'not_configured'}}
        local settings = decoded(GetSettingsConfig)
        local key
        for _, group in pairs(settings.values or {}) do
            if type(group) == 'table' and type(group.hubcapApiKey) == 'string' and group.hubcapApiKey ~= '' then key = group.hubcapApiKey end
        end
        if key then
            local stats = decoded(GetHubcapStats, key, true)
            result.hubcap.state = stats.success == false and (stats.errorType == 'rejected' and 'rejected' or 'unavailable') or next(stats) and 'verified' or 'unavailable'
            for _, field in ipairs({'daily_usage','daily_limit','remaining','limit','used','reset','retry_after'}) do
                local value = stats[field]
                if type(value) == 'number' and value >= 0 then result.hubcap[field] = value end
            end
        end
        return cjson.encode(result)
    end
    local methods = {
        authentication_status = authentication_status,
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
