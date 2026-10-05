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
   the main-menu downloader, select an existing empty destination, and download
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

This is currently a **single-depot test workflow**, not a complete installation
method. Files remain in the chosen folder's `.playlite-download` staging folder;
no automatic installation/library entry or multi-depot orchestration is enabled.
Cancellation removes the named worker and retains incomplete output. A real
Steam-authenticated Linux download was verified end to end with Baba Is You
(App ID 736260, depot 736263), using a supplied Hubcap binary manifest and a saved
Steam session through the existing isolated VPN. Game launching has not been tested.

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
