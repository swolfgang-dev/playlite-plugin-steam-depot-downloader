# Steam Downloader for Playlite

Requires Playlite 0.2.88 or later. The default downloader uses Hubcap/LuaTools
manifests and an isolated depot worker to download verified content from Steam's
CDN. No VM or Steam desktop is required. Provider requests, Steam authentication,
metadata lookups and game downloads share the existing guarded NordVPN namespace.
The host Steam installation and host VPN configuration are not used.

## Depot worker setup

Open **Settings → Plugins → Steam Downloader** (also offered on installation):

1. Choose **Build worker**. This requires existing Docker access and Git. The
   pinned DepotDownloaderMod source is built using a temporary Microsoft .NET SDK
   container, without installing a host SDK or system packages. Sources, NuGet
   state and publish output are temporary. Docker retains the SDK, gateway and
   worker images and its normal build cache. Tooling downloads use Docker/Git's
   normal connection; account and game traffic uses the isolated VPN.
2. Save your **NordVPN service credentials**, then choose **Connect VPN**. These
   are OpenVPN service credentials, rather than the VM backend's access token.
3. Choose **Steam login…** and complete password/Steam Guard authentication in
   Playlite. Use an account that owns the content. The reusable session is saved
   in KWallet; the Steam password is not stored.
4. Enter a fresh LuaTools Discord `/login` code through **LuaTools login…** and
   save your **Hubcap API key…**. Choose the preferred manifest provider; the
   other provider is tried when matching content is unavailable.

Credentials and provider/Steam sessions use KWallet and are independent of VM
sessions. Host prerequisites are not installed automatically. Existing VM data
and games are retained.

## Direct depot downloads

Open **Playlite menu → Steam Downloader…**, search by name or App ID, choose
Windows or Linux, language and optional DLC, then add the game to Downloads.
The **Sign in…** menu contains Steam, LuaTools, Hubcap and downloader setup.
The plugin folder override takes precedence over General's installation folder.
New jobs require an empty destination.

Each job loads current Steam depot/package metadata, resolves exact manifest
versions, checks a CDN sample, then downloads and validates the selected depots.
Missing manifests or failed authentication/CDN checks stop the job. Workers are
unprivileged and share the verified VPN gateway; the tunnel-only firewall blocks
traffic outside the VPN. The VM/desktop runtime is never started.

Queue history contains selections and the account name, without credentials,
manifest payloads, depot keys or live sessions. After a restart, unfinished jobs
return paused. Resume/retry refreshes metadata and provider data and validates
retained partial files under `.playlite-download`. Completed content is promoted
without overwriting destination files. Cancelled or failed jobs retain partial
content. Completed jobs support folder access and **Add to Playlite**. Selecting
DLC downloads content; it does not change licenses or launch configuration.

The VPN remains available while the downloader or queue needs it. The optional
session preference retains only the VPN. Removing the plugin retains downloaded
games, existing VM data and Docker images.

## Legacy VM and Steam desktop backends

Set `PLAYLITE_STEAM_BACKEND=vm` to select the retained VM backend. Its queue
records resume only in that backend; depot jobs resume in the default backend.
`PLAYLITE_STEAM_BACKEND=docker` selects the older Steam desktop container flow.
The following instructions describe these legacy backends.

The VM backend uses a private Linux VM
with Steam, LuaTools/LuaMoon, NordVPN and Wine. Host Steam files and host VPN
credentials are not used. The optional Docker compatibility backend is described
below.

## Setup and authentication

Install the plugin through Playlite's plugin settings, then open
**Settings → Plugins → Steam Downloader → Setup and sign-in…**. Installation
also offers this wizard. Opening the downloader itself does not start setup.

1. **Configure:** choose the shared game folder and enter a NordVPN access token
   from Nord Account. The token is encrypted in KWallet so setup can resume.
2. **Create VM:** provision the desktop, install NordVPN, sign in, connect and
   verify VPN protection.
3. **Install Steam:** download the verified Steam bootstrap, update Steam, and
   install LuaTools/LuaMoon over the guest VPN.
4. **Steam sign-in:** open the dedicated VM viewer and complete Steam's normal
   login and Steam Guard approval. Remember the account in Steam to retain its
   guest session. Playlite never stores the Steam password. Completion closes
   Playlite's viewer and advances setup while retaining the guest session.
5. **LuaTools and Hubcap:** enter a fresh LuaTools Discord `/login` code and an
   optional Hubcap Manifest API key, verify, then choose **Open Downloader**.

Stages 2–3 continue automatically. Progress is retained when the wizard closes;
reopen it to resume. Transient setup transport failures receive bounded retries.
Invalid credentials and account approvals still require attention. First boot
allows up to 20 minutes for the guest agent to become available.

**Maintenance → Saved credentials…** manages credentials encrypted in KWallet.
Saved values are applied inside the VM; expired LuaTools codes need replacement.
Provider sessions and Steam account state remain in the guest. **Open VM desktop**
opens the desktop without creating another setup flow. The host browser is used
only for explicit account links.

Host requirements and the standalone installer are documented in
[tools/vm/README.md](tools/vm/README.md). The host needs x86-64 Linux, KVM, a working
libvirt user session and the listed tools. Setup installs guest dependencies;
it does not install missing host packages or change the host VPN/firewall.

## Downloads and library integration

Open **Playlite menu → Steam Downloader…**, search by name or App ID, choose
Windows or Linux, language and optional DLC, then add the game to Downloads.
Windows is preferred when available. The VM Steam client resolves and verifies
content. Its public branch and automatic architecture selection are used.
Steam may also install compatibility runtimes privately in the guest.

Games install directly into the shared folder, normally Playlite's General
installation folder. Plugin overrides take precedence. Playlite records Steam's
actual installation directory and uses those files without exporting a second
copy or automatically uninstalling the shared installation. **Add to Playlite**
opens the normal add-game editor for executable and launch-action review.
Workshop files use `<shared>/Workshop/<AppID>/<ItemID>/`.

The Downloads panel supports sequential jobs, pause/resume, cancellation, retry,
progress, completed-folder access and library handoff. Queue history survives
application restarts; unfinished items return paused. Saved choices exclude
credentials, provider keys and live worker objects. Cancellation retains partial
files. Transient download failures share a three-retry budget with waits of
5, 15 and 30 seconds. Steam agreements require explicit acceptance.

Before installation, public retail package membership limits base-game depot
selection. Shared dependencies, app IDs and DLC retain their separate rules.
This is the union of public retail packages, rather than the account's exact
purchased edition. Missing package metadata stops preparation. Original scripts
and key-cache entries are retained privately for recovery. A changed selection
restarts the guest client; a failed selection update restores the prior active
script, keys and selection record.

The live log includes readiness, provider quota observations and errors.
Reported usage, remaining requests, Retry-After and reset values come from
existing responses, without additional quota requests. Missing counters are
identified as unavailable; HTTP 429 alone does not establish daily exhaustion.
Authentication headers, cookies and API keys are excluded from quota records.

## Storage and session lifetime

VM data defaults to
`${XDG_DATA_HOME:-~/.local/share}/playlite/plugin-data/SteamDepotDownloader/steam-vm`.
`PLAYLITE_STEAM_VM_ROOT` selects another owned profile. The private disk, account
state and installer files stay there. Temporary transfers use the separately
exported `download-storage` directory. Shared games stay outside VM userdata.

Changing the shared folder in plugin settings restarts the VM; existing games
remain in their original folder. Changes are blocked during queued or active
downloads. Failed service registration or restart restores the previous profile
configuration. Do not let two Steam instances update the same shared game files.

Closing downloader/settings windows retains active VM work. When downloads,
setup and guest jobs are idle and the VM has been unused for a minute, Playlite
shuts it down. Opening setup or starting a download starts it again. A running
host game does not keep an otherwise idle VM alive. Normal application exit
requests session cleanup; active guest work can retain the VM. Shutdown waits
for local jobs to finish or cancel safely.

Setup diagnostics use
`${XDG_STATE_HOME:-~/.local/state}/playlite/steam-vm-setup.log`, with **Copy log**
and **Open log** controls. Repository profiles keep their own data and state.
VM command failures include relevant stderr or service journal output.

## Removal and recovery

Removing the plugin retains the VM and shared games. **Maintenance → Delete VM…**
explicitly confirms removal of the owned domain, sharing services, private disk
and guest logins. Shared games and Workshop files remain. Downloads and active
setup block deletion. Domain disk and sharing-service ownership are checked
before removal.

Uninstalling a game from Steam itself deletes its shared installation files.
An exported copy in another directory remains separate. Never interpret removing
a library entry or plugin as a request to delete external game files.

The standalone installer refuses unrelated nonempty userdata and foreign domain
names. Interrupted downloads and setup can be retried in the marked profile.
A stopped domain already defined by that same installation is recognized through
its disk and sharing sockets. Existing successful installations are retained.

## External components

The bundled Moon installer recipe is pinned and checksum-verified. It resolves
component releases at setup time, so the resulting Moon stack is not fully
version-pinned. Upstream accepts some releases without published checksums.
The recipe executes inside the dedicated guest or compatibility container.
Steam, provider services and their UI interfaces can change independently.

The source installer and its AGPL license, the adapted Moby seccomp profile and
its Apache license, and attribution details are in `tools/steam/`. The separate
Windows SteamAutoCrack recipe retains its upstream license and verifies the
pinned application and runtime downloads. See the VM README for details.

## Docker compatibility backend

Set `PLAYLITE_STEAM_BACKEND=docker` explicitly to use the retained backend. It
requires Docker access, `socat`, user namespaces and NordVPN service credentials.
Setup builds the private Steam image, connects the isolated VPN, bootstraps
Steam and installs LuaMoon. Steam/provider sign-in uses its private desktop.
KWallet stores service credentials and independent provider/Steam sessions.

Workers join the exact verified gateway namespace, use UID 65534, drop
capabilities and use a read-only root with restricted mounts. The UID firewall
allows worker traffic only through `tun0`; IPv6 is disabled. Credentials are
private read-only files, removed after disconnect or failure. An independent
crash guardian retries owned-resource cleanup if Docker is unavailable.
Foreign and unlabelled resources are retained.

The nested Steam sandbox uses the supplied seccomp profile and container-local
`apparmor=unconfined`; it retains the other worker restrictions. The desktop
uses loopback relays and private Unix sockets. It never mounts host Steam or the
Docker socket. Its Openbox configuration preserves existing input bindings and
avoids redundant focus events that dismiss Steam popups.

Unlike the VM, this backend verifies and copies completed content into a new or
empty destination. It refuses to overwrite existing files, preserves partial
staging on failure and verifies copies before publishing them. Filesystems
without no-overwrite rename support use exclusive links/copies. Private source
cleanup occurs only after successful export; a cleanup failure leaves the job
Complete with a recovery message. Explicit private-content uninstall preserves
exported copies.

Docker session preferences can retain Steam and/or VPN until Playlite exits.
Keeping Steam also retains its VPN; retaining only VPN stops idle Steam.
These preferences do not initiate connections. Authentication popups retain the
parent session. Explicit disconnect and application exit request cleanup.

The retained depot-worker modules support bounded app/depot metadata, strict
Lua-as-data parsing, binary-manifest fallback across Luie/Hubcap/Sushi/Ryuu,
shared-depot and DLC planning, branch/language/architecture selection, and a
verified CDN sample before queueing. They require a separately built native
worker and independent Steam authentication. Ryuu's legacy HTTP endpoint lacks
transport authenticity. These modules remain compatibility infrastructure and
are not the normal VM downloader flow.

## Development and verification

Build archives with `python3 tools/build_release.py` and the standalone VM
installer with `python3 tools/build_vm_installer.py`. Release packaging includes
tracked source files, required assets and licenses. Build the optional native
worker with `python3 tools/build_worker.py --work /tmp/playlite-worker-build`;
this requires .NET SDK 10, Docker and upstream source downloads.

From the sibling core checkout, use `tools/check_workspace.py
playlite-plugin-steam-depot-downloader` with the existing project interpreter.
It creates disposable profiles and copies local plugin fixtures. Unit/UI tests
mock VM, Docker, provider and game operations. They do not establish current
end-to-end compatibility with account services or real games.

Opt-in Docker diagnostics are `tools/check_isolation.py`,
`tools/check_cleanup.py`, and `tools/check_steam_network.py`. These create and
remove their own containers. `tools/check_vpn_failures.py --live` additionally
uses a separate real VPN connection with the current gateway's read-only secret
mount; `--crash` enables its forced OpenVPN crash check. Run these deliberately,
not as part of a routine local code review.
