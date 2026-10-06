# Steam Downloader

## Install and get started

Install [Playlite 0.2.41 or later](https://github.com/swolfgang-dev/Playlite/releases/latest) by downloading `install.sh` and running `bash install.sh` from a normal terminal without sudo. On first launch, open the **Plugins** tab in **Get started**, select **Steam Downloader**, and click **Install selected plugins**. You can also install it through **Settings → Plugins → Available**.

Installation opens the isolated Steam setup window. Steam setup requires an x86-64 Linux host, a running Docker daemon accessible to your user, `socat`, enabled user namespaces, NordVPN service credentials, and internet access. These host dependencies must already be installed; the setup button builds the private Steam desktop and installs LuaMoon inside it.

1. Authenticate NordVPN, then click **Set up isolated Steam**.
2. Open the private desktop and sign into Steam and LuaMoon/providers there.
3. Restart Playlite to load the newly installed plugin.
4. Open **Playlite menu → Steam Downloader…**, search for your game, choose its platform (Windows by default), language and destination, then add it to Downloads.

Progress appears in the downloader log and Downloads panel. Downloads support pause, cancel and retry, and history remains across sessions until cleared. Successfully exported game files remain in your selected destination; the private source copy is removed after export verification. Host Steam is not used.

## Remove

Use **Settings → Plugins → Installed → Delete selected**. Steam Downloader offers optional removal of its owned Steam environment and private data/logins. Exported game folders, host Steam, Docker, and shared dependencies are retained. Resources created before ownership tracking are preserved.

The rest of this document records earlier development work and experimental depot-download paths. The supported release flow is the isolated Steam workflow above.


The current download flow uses an isolated Steam client with LuaMoon. In plugin settings, authenticate NordVPN, choose your default download location, and press **Set up isolated Steam**. Setup checks Docker access and Linux user namespaces, builds or updates the container image, connects the saved VPN credentials, bootstraps Steam, installs LuaMoon, and opens the private desktop. Steam and provider logins happen there and persist in the container home volume.

The host needs x86-64 Linux, Docker with user access, `socat`, internet access, and sufficient storage for the image, Steam/Proton, the private library, and exported game files. First-time Steam Guard and provider authentication remain interactive. Setup displays its build and installation progress and can be retried without deleting saved sessions. It is unavailable while downloads are queued.

Open **Steam Downloader** from the Playlite menu, search for a game, select Windows or Linux and optional DLC, and add it to Downloads. Store metadata supplies the chooser; Steam and LuaMoon resolve installation content and manifests inside the isolated environment. Provider credentials, depot backend selection, architecture overrides, and branch controls are no longer exposed. Games are verified by Steam before their files are copied to the selected destination. Windows is preferred when available.

The sections below document the previous depot workflow and the isolation implementation for maintenance; the legacy downloader is not exposed in the current UI.

---

# Steam Depot Downloader — experimental isolated downloads

Provides dedicated NordVPN/OpenVPN networking and an experimental Moon-compatible manifest provider plus independently authenticated Steam depot worker. It does not access host Steam, host Steam account files, or installed games. The workflow currently downloads a single selected depot into staging.

## Setup

Install Docker Engine with permission to run containers and ensure `/dev/net/tun` exists. KDE KWallet must be available for credential storage. Open Playlite Settings → Plugins → General → Steam Depot Downloader.

1. Get your **service username and password** from Nord Account's manual setup section; these are different from your normal account login.
2. Enter them and click **Save credentials**. KWallet may ask to unlock your wallet.
3. Optionally choose a country (for example `Canada`). Leave it empty for automatic selection across supported countries. UDP is the default; TCP is also supported.
4. Click **Connect**. Connection progress shows elapsed time; **Cancel connection** stops the attempt and cleans up temporary credentials. Authentication rejection is reported immediately. Gluetun chooses a compatible server automatically. **Check connection** verifies the tunnel, firewall, health and isolated public IP. **Disconnect** removes this plugin's container and temporary credential files.

Connection controls apply immediately. Save in Playlite settings persists only country and protocol. Existing host VPN connections and Gluetun containers are not changed. No host ports are published.

## Isolation

The gateway uses a pinned Gluetun image and a dedicated Docker bridge namespace. Probe workers join its exact container ID, run as UID 65534 without capabilities, with a read-only filesystem, and do not receive a Docker socket or host home directory. A UID-specific OUTPUT firewall permits worker traffic only through `tun0`. Gluetun's own firewall also remains enabled. Worker DNS uses NordVPN DNS addresses through the same tunnel; IPv6 is disabled in the namespace. There is no worker during VPN bootstrap, and readiness checks must pass before probing public connectivity.

Service credentials are stored in KWallet. OpenVPN receives private, read-only mounted secret files rather than command-line arguments or environment values. These files are removed on disconnect or connection failure. While connected, host root and Docker administrators can access them, as they can access the container itself.

This preview does not autoconnect on startup. Normal Playlite exit disconnects the session. An independent helper monitors the owning process and removes this plugin's workers, gateway and temporary credential files after a crash. It retries if Docker is temporarily unavailable. KWallet credentials remain saved. Closing to the system tray keeps Playlite and its VPN running.

An explicit **Connect** can replace a leftover connection belonging to this plugin and user, after validating its image, network settings, and private read-only credential mount. It refuses foreign or unsafe containers. Docker gateway restarts invalidate the namespace of existing workers; a new worker must be created after restoring the guard and passing readiness checks.

## Validation and next milestone

Twenty-one unit/UI tests cover credentials, container ownership, permissions, normalized firewall rules, authentication errors, cancellation, stale health, worker ordering, shutdown and cleanup safety. The offline Docker tests cover TCP and DNS/UDP blocking, tunnel loss, forced fallback routes, tunnel recovery, namespace replacement, IPv6, and actual parent-process crash cleanup. GitHub CI runs those credential-free Docker tests before building releases.

Live validation on 5-Oct-2026 passed all nineteen checks using a separate NordVPN connection: diagnostic HTTPS and DNS, blocked LAN access, stopped-tunnel and forced-fallback blocking, OpenVPN stop/start recovery, forced process-crash recovery, gateway replacement, stale-worker blocking, and a fresh worker on the recovered gateway. OpenVPN was killed with SIGKILL; Gluetun automatically started a replacement process and restored HTTPS and DNS without a manual reconnect. The user's original connection stayed healthy. Earlier attempts encountered intermittent NordVPN authentication rejection; the successful run verifies automatic recovery but does not establish the cause of those earlier rejections. A single-depot download test workflow is now available; end-to-end authenticated content verification remains pending.

Run the offline checks with `python tools/check_isolation.py` and `python tools/check_cleanup.py`. To explicitly authorize a separate live test connection using the current gateway's read-only secret mount, run `python tools/check_vpn_failures.py --live`; optionally select `--country "United States"`. Add `--crash` to include the verified forced OpenVPN process-crash test. The live test never prints credential contents and cleans up its own containers.

## Native worker prototype

The experimental `worker.py` constructs a separate unprivileged downloader container only after VPN readiness checks pass. It mounts a caller-created staging folder and optional read-only prepared manifest/key inputs. Its home and authentication cache live in an ephemeral tmpfs; host Steam files are never mounted. The plugin UI still has no enabled download action.

`tools/build_worker.py --work /tmp/playlite-worker-build` builds the pinned DepotDownloaderMod source revision `c0f62fb7f020087f36ae76adfc51fde1446af344` with .NET SDK 10 and a self-contained Linux/musl runtime. Use `--dotnet /path/to/dotnet` if necessary. The work directory preserves the GPL upstream source, license and .NET 10 project changes. The Docker image adds libgcc/libstdc++ and runs as UID 65534.

The Baba Is You test (App ID 736260) successfully connected anonymously to Steam inside the VPN namespace. The requested depot key returned `AccessDenied`, with zero bytes from zero depots. Upstream returned process exit code zero despite that failure; the worker explicitly rejects this false success.

Baba's Moon pack was not cached in the VM. Its public configured sources did not provide a pack, and the saved Moon access token was expired. Session renewal returned HTTP 401. A fresh Moon login/manifest pack, or a separately authenticated Steam session, is required before the actual small-file download test can proceed. No game content has been downloaded yet. Twenty-five unit/UI tests pass, including worker isolation and false-success handling.

Next milestone: verify the new provider/login workflow with an actual small file, then add complete multi-depot installation orchestration. No integration with host Steam is planned.

Gluetun documentation: https://github.com/qdm12/gluetun-wiki
NordVPN service credentials: https://support.nordvpn.com/hc/en-us/articles/19685514639633

### Moon providers and independent Steam login (experimental)

Open **Playlite menu → Steam Depot Downloader…** after connecting the VPN.
Connection preferences remain in **Settings → Plugins → General → Steam Depot
Downloader**. Use **Manage authentication…** there for Moon login/sign-out,
Hubcap API keys, and Steam login/forget controls. The native worker image must be built using `tools/build_worker.py` first.
It now includes Python for isolated provider HTTP requests, as well as .NET for
depot downloads. The worker source patch adds a login-only mode and keeps its
account cache in the explicitly mounted private authentication directory. Main
menu and settings plugin instances share the same VPN runtime for the app session.

1. Enter the Steam App ID and select a provider. Luie uses Moon's six-character
   login-code flow (run `/login` in the LuaTools Discord → redeem the six-character code → verify magic-link token). Hubcap
   uses its own API key. Sushi and Ryuu are also supported; Ryuu's endpoint is
   HTTP, as in Moon, and does not provide transport authenticity.
2. From the Playlite menu, fetch the pack and select a depot/manifest. Lua is parsed as data and never
   executed. ZIP traversal, symlinks, conflicting pins/keys, wrong App IDs and
   oversized packs are rejected. Manifest bytes and keys never enter host Steam.
3. In plugin settings → Manage authentication, sign into the Steam account that
   owns the game. The dedicated login-only worker does not request game data.
   Enter the password and Steam Guard responses when prompted there. Return to
   the main-menu downloader, select an empty or new destination, and download
   using that saved session. If Steam requires reauthentication, the download
   stops and directs you back to settings. These responses travel on
   stdin, not command-line arguments. Each worker uses its own random Steam
   LogonID, ephemeral HOME/authentication cache and the VPN's guarded namespace.

Moon sessions are encrypted in KWallet, restored on the next provider request,
and refreshed automatically. Sign out removes the saved Moon session. Steam's
reusable login token is also encrypted in KWallet, separately for each account;
the Steam password is never saved. A temporary private authentication directory
is mounted only for that worker, then removed after its session is returned to
KWallet. Forget Steam login removes the saved account session. Steam Guard may
still be required when Steam invalidates or expires a session.
Provider requests fail closed if the VPN checks fail; authenticated HTTP redirects
are rejected so provider credentials cannot be forwarded to another service.

The downloader searches Steam by name or App ID, selects the game and fetches its
manifest pack automatically. An authenticated metadata-only worker reads Steam app
and depot information through the isolated VPN. Linux is selected when Steam lists
Linux content; common files and matching English/64-bit platform files are included.
Optional DLC appears as a checklist, with content bundled into base-game files
explained separately. DLC without separate files does not trigger another download. **Advanced** holds
the provider, manual fetch and depot IDs. **Show details** opens the raw log.
Progress shows bytes, percentage, average transferred speed and an estimated time.

Set **Default location** under plugin settings → Downloads. The downloader suggests
`<default location>/<game name>` and creates missing directories when Download is
pressed. Existing nonempty folders are rejected. Files download into hidden staging,
then validated output is promoted with atomic no-overwrite renames. Filesystems
that reject those rename flags (including mergerfs) use exclusive hard links or
verified copies and exclusive directory creation instead; existing files are never
replaced. Failures retain
staging. **Open folder** opens the completed destination; **Add to Playlite** lets you
choose an executable and review the normal add-game editor before saving.

The download queues every required base-game depot and the separately selected DLC
depots. Extra DLC manifest packs are fetched only for selected DLC whose manifests
are absent from the base-game pack. Missing manifests stop preparation before any
game files are downloaded. Depots run sequentially in the same staging folder;
output is promoted only after every depot completes and validates. A failed or
cancelled depot stops the queue and retains incomplete staging. The destination
must be empty, and completed output is never merged into existing user files.

Steam authentication is required. Package ownership is not used as a blanket
availability gate; Steam's depot/CDN response determines whether downloading
succeeds. No host Steam client, installation or authentication files are accessed.
Metadata discovery is capped at 100 DLC apps and requests no game files.

Rebuild the native worker using `tools/build_worker.py` after updating this plugin,
because DLC discovery requires the new `-app-info` worker mode. The build applies
the metadata patch in `tools/appinfo_patch.py` to the pinned upstream source.
Steam-authenticated Linux downloads and finalization were verified with Baba Is You
(App ID 736260, depot 736263), using a Hubcap binary manifest and a saved Steam session.
Game launching has not been tested. The downloader reuses an existing isolated VPN;
otherwise it connects with saved credentials and disconnects that connection on close.

Connection failures caused by authentication rejection, tunnel timeout or a stopped
gateway are retried up to five total attempts, with waits of 5, 10, 20 and 30
seconds. Each attempt has its own 90-second tunnel readiness timeout. Cancellation
also interrupts retry waits. Unsafe configuration and Docker errors are not retried.

Automatic selection queries NordVPN's public recommendation API for the chosen
OpenVPN protocol, optionally restricted to the selected country. It follows the
returned ranking and checks that the hostname exists in the pinned Gluetun
server catalogue. Retries skip previously attempted recommended servers. If the
API is unavailable or all compatible recommendations are exhausted, selection
falls back to Gluetun's compatible-server pool. Only public VPN bootstrap metadata
is requested outside the tunnel; provider requests and Steam traffic stay isolated.

In the downloader, enter a game name in **Game / App ID** to search the same Steam
store search endpoint used by Steam Metadata. Select a result to fill its App ID.
Search waits 650 ms after typing, requires three characters, and shows at most eight
results with cover artwork. Numeric IDs bypass search. Covers are cached for up
to 32 games per dialog; changing the query stops the old cover queue. Search and
artwork requests use the verified isolated VPN transport.

With an updated Playlite core, **Add to queue** transfers a prepared game download
into the application Downloads tray. The selection window is non-modal and can
close while queued downloads continue. Games run sequentially, with their base-game
and selected DLC depots grouped together. Closing plugin settings defers VPN cleanup
while downloads are queued or active. The tray supports removing waiting games,
cancelling the current game, inspecting progress/status, opening completed folders,
and adding completed games to Playlite. Queue history is in memory for this app
session; closing Playlite cancels pending jobs rather than persisting credentials or
manifest keys to a queue file.

DLC names use native Steam metadata, cached names, Steam Store metadata, matching
provider depot names, and accessible SteamDB page titles with a verified App ID.
There are no individual DLC title overrides. Successful names are cached for 30
days; unsuccessful lookups for one hour. External name lookups share a budget of
eight requests per game and use the isolated VPN. Unresolved entries show
**Unknown DLC (App ID)**; a hidden or unnamed store listing alone does not prove
that its content cannot download.

DLC tooltips distinguish available manifests from additional provider requests.
Selected manifests are prepared before queueing; failures are displayed on the
DLC while preserving its selection so the user can retry or explicitly uncheck it.
Manifest presence does not confirm Steam/CDN access. Authentication, CDN access,
network interruptions, and filesystem errors can still fail during a download.

Download planning now follows Steam's depot metadata order, including
parent-managed DLC in its original position. Separately managed DLC is appended
in discovery order, and shared depots inherit their source app's OS, language,
architecture, DLC association, and manifest settings. Source app metadata requests
are bounded to 20, with cycle detection. Later depots can replace earlier files
inside private staging; existing destination files are never overwritten.

**Advanced** exposes language, architecture, and unprotected branch choices.
Defaults are English, 64-bit, and public. Provider manifest IDs must match Steam's
selected branch (falling back to public where the branch has no depot override);
stale or mismatched packs stop before queueing. Password-protected branches are
not supported. DLC owned by the separate Steam account is selected initially;
users can change that selection, and package ownership is not a download gate.

Before queueing, each depot is checked in the verified VPN using the saved Steam
session. The worker decrypts its supplied manifest and downloads one small CDN
chunk into memory, verifying it through SteamKit. These samples are real network
transfers, but no game files are installed. Empty depots are recorded separately.
Authentication or CDN failures prevent queueing. A successful sample confirms
access at that moment; later chunks, expired sessions, network interruptions and
disk errors can still fail. The native worker must be rebuilt with the current
metadata/preflight patches using tools/build_worker.py. Host Steam is never read.

The selected manifest provider is preferred, with automatic fallback through the
other configured providers (Luie, Hubcap, Sushi and Ryuu). Required base-game,
selected DLC and shared-depot manifests can be combined across providers. Only
manifests matching Steam's selected branch are accepted. Each provider/App ID
pair is fetched once per preparation, with a 128-request ceiling. Missing login
credentials, browser challenges and HTTP failures are recorded and do not stop
fallback to the remaining providers. Detailed failures are available through
**Downloader log**; no game is queued until every required manifest and CDN sample
passes. Provider requests continue to use the isolated VPN.

Providers can return plain Lua metadata or ZIP packs containing Lua and/or binary
manifests. Lua is parsed strictly as data and is never executed. Depot keys without
manifest pins are bound to the current Steam branch manifest. When a Lua-only row
is available, remaining providers are checked for its exact binary manifest;
matching binaries can be combined with the Lua key. If no provider supplies the
binary, the isolated worker requests it from Steam. Steam can reject this request
even when provider metadata is available; the CDN preflight still must succeed.

Base-game depot selection uses the authenticated account's relevant Steam package
depot lists. If no relevant account package is available, public store package IDs
are resolved through authenticated Steam package metadata. This excludes content
outside those packages, including developer depots, without a game-specific deny
list. When package metadata is unavailable, selection falls back to app metadata.
Manual DLC selections remain available; package ownership is not a download gate.

The downloader shows its live log below the download folder, with a Copy log action. Manifest
lookups, readiness checks, errors and worker output stay available across attempts.
The selection window has no visible progress bar; download progress remains in
the Downloads panel.
The Downloads list is saved across Playlite sessions. Completed items remain
until **Clear finished** is clicked. Unfinished transfers and waiting items are
restored paused; **Resume** or **Retry** reuses their saved game, platform,
language and destination. Partial files are retained. **Cancel** stops an item
without deleting its files. The list orders active downloads, queued items,
completed items, stopped/failed items, then paused items. Closing Playlite with
unfinished downloads asks for confirmation and preserves them for resuming.
Persistence excludes credentials, provider keys and live worker objects.

Steam and the isolated VPN stay active while plugin settings, the downloader,
or the Downloads panel is open, or downloads are queued or running. They are
released once all those windows close and the queue is idle.
Settings offer **Keep Steam open for the Playlite session** and **Keep VPN
connected for the Playlite session**, both off by default. Keeping Steam open
also keeps its VPN connected. Keeping only the VPN connected stops idle Steam.
These preferences retain an existing session; they do not start a connection.
Manual Disconnect and Playlite exit still stop both.
**Stop Steam environment** in settings manually releases the private desktop
while retaining the VPN and saved login/game files, even with Keep Steam open
enabled. Pause or cancel queued and active downloads first. Open Steam desktop
starts it again.
The private Openbox desktop focuses client windows only when they are not already
focused. This avoids redundant focus events dismissing Steam dropdowns and
context menus before their click handlers run (see
[Valve's Linux issue](https://github.com/ValveSoftware/steam-for-linux/issues/9273#issuecomment-1766922429)).
Authentication popups do not release the connection when closed.
Provider responses during LuaMoon preparation also produce **API limits** log
entries: reported daily usage/limit, remaining requests, Retry-After, and reset
values. These are observations from existing requests, not extra quota checks.
Providers that omit counters or cooldowns are explicitly labelled as not reporting
them. HTTP 429 means request throttling and is not automatically called a daily
quota exhaustion. No authentication headers, cookies, or API keys are logged.

### Experimental isolated Steam environment

Build the optional desktop image with `docker build -t playlite-steam-runtime:test tools/steam`,
then open **Isolated Steam setup** from the main menu while the isolated VPN is connected.
The desktop is accessible through a loopback browser relay and private Unix sockets.
Steam uses a dedicated home volume and library; host Steam files are never mounted.
Keep plugin settings open during setup, since closing idle settings releases the VPN.

Steam's nested sandbox needs the supplied seccomp profile and container-local
`apparmor=unconfined`: Docker's default AppArmor policy blocks its namespace mounts.
The runtime still uses UID 65534, no capabilities, no-new-privileges, a read-only
root filesystem, restricted mounts and the VPN worker firewall.

Install Moon from the setup dialog, then authenticate its providers inside isolated
Steam. Playlite's existing KWallet provider sessions are not imported into Moon.
The installer script is pinned and checksum-verified; the upstream installer selects
component releases. The downloader's **Download using** selector defaults to
**Steam (LuaMoon)**. Each queued item captures its platform, language and DLC choices.
Windows selects Proton in the isolated client to request Windows depots; Linux
clears the per-game compatibility override. The client installs into the registered
**Playlite downloads** library (`/library`), then verified files are copied without
overwriting into the folder selected in the downloader. Known wrong-platform depots
prevent completion. Queued Steam downloads keep the VPN alive; after the queue
finishes, open windows and the session preferences determine when it is released.

The Steam backend currently uses the public branch and Steam's automatic architecture
selection. Choose **Depot downloader** for a custom branch, 32-bit architecture or
macOS: the Linux Steam client cannot natively install macOS content. Steam may also
download its compatibility runtimes; those stay in the private library and are not
copied into the game folder. Steam login/EULA prompts may still need attention in the
isolated desktop. The private bridge exposes bounded actions, not arbitrary JavaScript;
Lumen verifies the Steam debugger process before applying content selections.

Download progress uses Steam's local-client download overview callback. Manifest
counters remain a fallback when that callback is unavailable; they can lag behind
the actual transfer. A cold client receives a bounded startup wait before its
content API is queried. Exporting a completed game from the private default Steam
library runs asynchronously and reports copied bytes, keeping client-status
requests responsive until the files are ready. Older running bridges receive a
longer status timeout during an upgrade so their synchronous copy can finish.

Before a queued installation, Playlite obtains public retail package IDs from the
Steam store and queries their depot membership through the isolated client's native
console. Base-game depot entries outside that membership are removed from LuaMoon's
script and SLSsteam key cache; the original data is kept privately for recovery.
Shared dependencies, app IDs and DLC entries retain their separate Steam selection
rules. This uses the union of public retail packages, not the account's exact purchased
edition. Missing package or app-depot metadata stops preparation instead of selecting
all provider depots. A changed depot set restarts the private client before installation.

Settings → Plugins → Steam Downloader → Isolated Steam → Manage installed content
queries only the private Steam libraries. Uninstall requires explicit confirmation,
blocks queued/active downloads and verifies removal from Steam before showing success.
Exported game folders remain separate and are not removed by this action.

After Playlite verifies and finalizes the exported files, it asks isolated Steam
to uninstall the private game copy and removes that game's intermediate export
cache. Failed exports retain their private source. If cleanup fails after a
successful export, the download remains Complete with a cleanup-pending message;
the exported installation remains usable.

Plugin installation opens the Steam environment installer, with NordVPN
authentication available directly in it. Plugin removal offers separate choices
to remove the environment and delete private data/logins. New volumes and images
receive installation ownership labels; private directories have a receipt.
Cleanup preserves resources without matching ownership, including older
unlabelled resources, external game folders and host/shared packages.
