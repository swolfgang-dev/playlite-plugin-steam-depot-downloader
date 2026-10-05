# Steam Depot Downloader — network setup preview

The first milestone adds a dedicated NordVPN OpenVPN connection for future native Linux depot downloads. **Game downloads are not implemented or enabled.** It does not access host Steam, Steam account files, Moon, or installed games.

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

This preview does not autoconnect on startup. Disconnect before quitting Playlite. If Playlite terminates unexpectedly, its dedicated container may remain; a subsequent connection refuses to overwrite it. Inspect and remove `playlite-steam-downloader-vpn-<your numeric UID>` through Docker before reconnecting. Runtime credential files can also remain after an abrupt termination.

## Validation and next milestone

Eleven tests cover unsafe settings, credential injection, worker privileges, container ownership, unhealthy tunnels, authentication rejection, normalized firewall rules, cancellation, temporary credential cleanup, and UI control recovery. An actual Docker namespace test verifies that a reachable local TCP endpoint becomes inaccessible to the worker after installing the firewall. Live VPN authentication and tunnel reconnection still require user credentials and further tests before any downloader is enabled.

Future work: connection lifecycle recovery, cancellation, repeated drop/reconnect tests, then an isolated standalone downloader. No integration with host Steam is planned.

Gluetun documentation: https://github.com/qdm12/gluetun-wiki
NordVPN service credentials: https://support.nordvpn.com/hc/en-us/articles/19685514639633
