"""Probe one bounded, verified CDN chunk without installing any game files."""
from pathlib import Path

METHOD=r'''
        public static bool PlaylitePreflight;
        private static async Task CheckPlayliteCdn(DepotDownloadInfo depot, DepotManifest manifest)
        {
            var chunk = manifest.Files.SelectMany(file => file.Chunks)
                .OrderBy(value => value.CompressedLength).FirstOrDefault();
            if (chunk == null)
            {
                Console.WriteLine("PLAYLITE_PREFLIGHT {0} {1} EMPTY", depot.DepotId, depot.ManifestId);
                return;
            }
            if (chunk.UncompressedLength > 4 * 1024 * 1024 || chunk.CompressedLength > 4 * 1024 * 1024)
                throw new ContentDownloaderException("CDN test chunk exceeds the size limit.");
            var buffer = new byte[(int)chunk.UncompressedLength];
            for (var attempt = 0; attempt < 3; attempt++)
            {
                var connection = cdnPool.GetConnection();
                try
                {
                    string token = null;
                    if (steam3.CDNAuthTokens.TryGetValue((depot.DepotId, connection.Host), out var promise))
                        token = (await promise.Task).Token;
                    var written = await cdnPool.CDNClient.DownloadDepotChunkAsync(depot.DepotId,
                        chunk, connection, buffer, depot.DepotKey, cdnPool.ProxyServer, token);
                    if (written != (int)chunk.UncompressedLength)
                        throw new ContentDownloaderException("CDN test chunk could not be validated.");
                    cdnPool.ReturnConnection(connection);
                    Console.WriteLine("PLAYLITE_PREFLIGHT {0} {1} OK", depot.DepotId, depot.ManifestId);
                    return;
                }
                catch (SteamKitWebRequestException error) when (error.StatusCode == HttpStatusCode.Forbidden && attempt < 2)
                {
                    try { await steam3.RequestCDNAuthToken(depot.AppId, depot.DepotId, connection); }
                    finally { cdnPool.ReturnConnection(connection); }
                }
                catch
                {
                    cdnPool.ReturnBrokenConnection(connection);
                    if (attempt == 2) throw;
                }
            }
            throw new ContentDownloaderException("Steam CDN access test failed.");
        }

'''

def apply(source):
    source=Path(source)
    p=source/'Program.cs';s=p.read_text()
    marker='                ContentDownloader.Config.DownloadAllLanguages ='
    if 'ContentDownloader.PlaylitePreflight =' not in s:
        if marker not in s:raise RuntimeError('Worker preflight entry point changed.')
        s=s.replace(marker,'                ContentDownloader.PlaylitePreflight = HasParameter(args, "-playlite-preflight");\n'+marker)
        p.write_text(s)
    p=source/'ContentDownloader.cs';s=p.read_text()
    if 'public static bool PlaylitePreflight;' not in s:
        marker='        private static async Task DownloadSteam3Async(List<DepotDownloadInfo> depots)'
        if marker not in s:raise RuntimeError('Worker CDN section changed.')
        s=s.replace(marker,METHOD+marker)
        marker='            if (Config.DownloadManifestOnly)'
        if marker not in s:raise RuntimeError('Worker manifest section changed.')
        s=s.replace(marker,'            if (PlaylitePreflight)\n            {\n                await CheckPlayliteCdn(depot, newManifest);\n                return null;\n            }\n\n'+marker)
        p.write_text(s)
