"""Add bounded Steam metadata export export to the worker."""
from pathlib import Path

METHOD = r'''
        public static async Task ExportPlayliteAppInfo(uint appId, uint[] publicPackages)
        {
            object Tree(KeyValue node) => node.Children.Count == 0
                ? (object)(node.Value ?? "")
                : node.Children.GroupBy(x => x.Name).ToDictionary(x => x.Key, x => Tree(x.First()));
            async Task<object> Info(uint id)
            {
                await steam3.RequestAppInfo(id);
                var owned = await AccountHasAccess(id, id);
                var licensed = (steam3.Licenses ?? []).Select(value => value.PackageID).ToHashSet();
                var relevant = steam3.PackageInfo.Where(pair => licensed.Contains(pair.Key)
                    && pair.Value != null && pair.Value.KeyValues["appids"].Children.Any(value => value.AsUnsignedInteger() == id))
                    .Select(pair => pair.Value).ToList();
                var packageSource = "account";
                if (relevant.Count == 0 && id == appId && publicPackages.Length > 0)
                {
                    await steam3.RequestPackageInfo(publicPackages);
                    relevant = steam3.PackageInfo.Where(pair => publicPackages.Contains(pair.Key)
                        && pair.Value != null && pair.Value.KeyValues["appids"].Children.Any(value => value.AsUnsignedInteger() == id))
                        .Select(pair => pair.Value).ToList();
                    packageSource = "store";
                }
                uint[] packageDepots = relevant.Count == 0 ? null : relevant
                    .SelectMany(package => package.KeyValues["depotids"].Children)
                    .Select(value => value.AsUnsignedInteger()).Where(value => value > 0).Distinct().ToArray();
                return new {
                    id, owned, package_depots = packageDepots, package_source = relevant.Count == 0 ? "unavailable" : packageSource, name = GetSteam3AppSection(id, EAppInfoSection.Common)?["name"].AsString() ?? "",
                    depots = Tree(GetSteam3AppSection(id, EAppInfoSection.Depots) ?? KeyValue.Invalid)
                };
            }
            await steam3.RequestAppInfo(appId);
            var ids = new HashSet<uint>();
            var depots = GetSteam3AppSection(appId, EAppInfoSection.Depots);
            if (depots != null)
                foreach (var depot in depots.Children)
                    if (uint.TryParse(depot["dlcappid"].Value, out var id) && id > 0) ids.Add(id);
            var extended = GetSteam3AppSection(appId, EAppInfoSection.Extended);
            foreach (var value in (extended?["listofdlc"].Value ?? "").Split(','))
                if (uint.TryParse(value.Trim(), out var id) && id > 0) ids.Add(id);
            if (ids.Count > 100) throw new ContentDownloaderException("This game has more than 100 DLC entries; metadata discovery is limited to 100.");
            var dlc = new List<object>();
            foreach (var id in ids) dlc.Add(await Info(id));
            Console.WriteLine("PLAYLITE_APPINFO " + System.Text.Json.JsonSerializer.Serialize(new { game = await Info(appId), dlc }));
        }

'''
BRANCH = r'''
            if (HasParameter(args, "-app-info"))
            {
                if (!InitializeSteam(username, password)) return 1;
                try {
                    var packageIds = (GetParameter<string>(args, "-package-ids") ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries)
                        .Select(value => uint.Parse(value)).Distinct().ToArray();
                    if (packageIds.Length > 64) throw new ContentDownloaderException("Package metadata request exceeds the limit.");
                    await ContentDownloader.ExportPlayliteAppInfo(appId, packageIds); return 0;
                }
                finally { ContentDownloader.ShutdownSteam3(); }
            }

'''
def apply(source):
    source = Path(source) / 'DepotDownloader'
    path = source / 'Program.cs'; text = path.read_text()
    if 'if (HasParameter(args, \"-app-info\"))' in text:
        start=text.rfind('\n',0,text.index('            if (HasParameter(args, \"-app-info\"))'))
        end=text.index('            var pubFile',start)
        text=text[:start]+text[end:]
    if 'ExportPlayliteAppInfo' not in text:
        marker = '            var pubFile = GetParameter(args, "-pubfile", ContentDownloader.INVALID_MANIFEST_ID);'
        if marker not in text: raise RuntimeError('Worker metadata entry point changed.')
        path.write_text(text.replace(marker, BRANCH + marker))
    path = source / 'ContentDownloader.cs'; text = path.read_text()
    if 'ExportPlayliteAppInfo' in text:
        start=text.index('        public static async Task ExportPlayliteAppInfo(')
        end=text.index('        internal static KeyValue GetSteam3AppSection(',start)
        text=text[:start]+text[end:]
    if 'ExportPlayliteAppInfo' not in text:
        marker = '        internal static KeyValue GetSteam3AppSection('
        if marker not in text: raise RuntimeError('Worker metadata section changed.')
        text = text.replace(marker, METHOD + marker)
        path.write_text(text)

    from preflight_patch import apply as preflight
    preflight(source)
