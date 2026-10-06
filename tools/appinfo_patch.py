"""Add bounded Steam metadata export export to the worker."""
from pathlib import Path

METHOD = r'''
        public static async Task ExportPlayliteAppInfo(uint appId)
        {
            object Tree(KeyValue node) => node.Children.Count == 0
                ? (object)(node.Value ?? "")
                : node.Children.GroupBy(x => x.Name).ToDictionary(x => x.Key, x => Tree(x.First()));
            async Task<object> Info(uint id)
            {
                await steam3.RequestAppInfo(id);
                return new {
                    id, owned = await AccountHasAccess(id, id), name = GetSteam3AppSection(id, EAppInfoSection.Common)?["name"].AsString() ?? "",
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
                try { await ContentDownloader.ExportPlayliteAppInfo(appId); return 0; }
                finally { ContentDownloader.ShutdownSteam3(); }
            }

'''
def apply(source):
    source = Path(source) / 'DepotDownloader'
    path = source / 'Program.cs'; text = path.read_text()
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
