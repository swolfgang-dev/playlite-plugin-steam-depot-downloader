"""Build the pinned GPL DepotDownloaderMod as a native Linux test worker.

Requires .NET SDK 10 and Docker. Does not access Steam or download game data.
The source and .NET 10 patches remain in the specified work directory.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

REPOSITORY = 'https://github.com/SteamAutoCracks/DepotDownloaderMod.git'
REVISION = 'c0f62fb7f020087f36ae76adfc51fde1446af344'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--image', default='playlite-depot-worker:test')
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    source = work / 'source'
    if not source.exists():
        subprocess.run(['git', 'clone', REPOSITORY, str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'checkout', '--detach', REVISION], check=True)
    project = source / 'DepotDownloader' / 'DepotDownloaderMod.csproj'
    project.write_text(project.read_text().replace('<TargetFramework>net9.0</TargetFramework>', '<TargetFramework>net10.0</TargetFramework>'))
    (source / 'global.json').write_text(json.dumps({'sdk': {'version': '10.0.100', 'rollForward': 'latestFeature'}}))
    program = source / 'DepotDownloader' / 'Program.cs'
    login_only = '''            if (HasParameter(args, "-login-only"))
            {
                if (!InitializeSteam(username, password)) return 1;
                ContentDownloader.ShutdownSteam3();
                Console.WriteLine("Steam authentication completed.");
                return 0;
            }

'''
    text = program.read_text()
    if '-login-only' not in text:
        marker = '            var appId = GetParameter(args, "-app", ContentDownloader.INVALID_APP_ID);'
        if marker not in text: raise RuntimeError('Pinned worker login entry point changed.')
        program.write_text(text.replace(marker, login_only + marker))
    accounts = source / 'DepotDownloader' / 'AccountSettingsStore.cs'
    text = accounts.read_text()
    text = text.replace('        static readonly IsolatedStorageFile IsolatedStorage = IsolatedStorageFile.GetUserStoreForAssembly();', '')
    text = text.replace('IsolatedStorage.FileExists(filename)', 'File.Exists(filename)')
    text = text.replace('IsolatedStorage.OpenFile(', 'File.Open(')
    accounts.write_text(text)
    downloader = source / 'DepotDownloader' / 'ContentDownloader.cs'
    text = downloader.read_text()
    text = text.replace('DepotManifest.LoadFromFile(Config.ManifestFile)', 'DepotManifest.Deserialize(File.ReadAllBytes(Config.ManifestFile))')
    downloader.write_text(text)
    context = work / 'image'
    context.mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__).parent / 'worker' / 'Dockerfile', context / 'Dockerfile')
    subprocess.run([args.dotnet, 'publish', str(project), '-c', 'Release', '-r', 'linux-musl-x64',
                    '--self-contained', 'true', '-p:SourceRevisionId=' + REVISION,
                    '-o', str(context / 'published')], cwd=source, check=True)
    subprocess.run(['docker', 'build', '--label', 'io.playlite.depot-worker.revision=' + REVISION,
                    '-t', args.image, str(context)], check=True)

if __name__ == '__main__': main()
