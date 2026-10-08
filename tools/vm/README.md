# Steam VM installer

Run from a terminal as your normal desktop user:

```sh
bash install.sh install --shared /mnt/SSD/Games/Standalone
```

The default userdata directory is:

```text
${XDG_DATA_HOME:-~/.local/share}/playlite/plugin-data/SteamDepotDownloader/steam-vm
```

Use `--root /another/userdata/folder` for a different profile. The VM disk, base
image, seed, installer copy, Steam profile, and NordVPN account state all live
there. Steam, NordVPN, and provider account state exists only after you sign in inside the
guest; the installer contains no credentials, SSH keys, or saved sessions.

The shared folder can be a mergerfs pool. Export the merged path, not one of its
physical disks. Steam downloads games into `<shared>/<Game>/` directly, and Playlite links to
those same files without copying or uninstalling them; Workshop content uses
`<shared>/Workshop/<AppID>/<ItemID>/`. Steam metadata is private to the VM.
Temporary downloads are in a hidden folder unique to this VM on the same pool.
Existing game directories are reused by Steam. Do not let two Steam instances
update the same game folder simultaneously.

The installer creates a new VM; it does not move or replace your existing VM.
It refuses foreign/nonempty userdata and an existing VM name. An interrupted
download or setup can be retried in its marked userdata directory. It checks the
Ubuntu image against Ubuntu's published SHA-256 manifest before creating the
disk. Installed VMs are retained on subsequent installer runs.

Host requirements: x86-64 Linux, working KVM, libvirt's user session,
`qemu-system-x86`, `qemu-utils`, `libvirt-daemon-system`, `virt-manager`,
`genisoimage`, and `uidmap`. The host user needs access to `/dev/kvm` and subordinate
UID/GID ranges. If virtiofsd is not installed, the installer extracts Ubuntu's
`virtiofsd` package into its private userdata. It does not change host firewall,
VPN, AppArmor, or system packages.

The host sharing daemon is a systemd user service, started when opening this VM.
It uses a namespace sandbox and maps guest file ownership to the desktop user.
The VM has 8 GiB RAM, four virtual CPUs, and a 160 GiB sparse disk. Internet uses
QEMU user networking. SPICE listens only on localhost; clipboard sharing is
enabled and SPICE file transfer is disabled. Only the chosen games folder is
exported as a filesystem.

First boot installs a minimal Ubuntu 24.04 XFCE desktop, Steam, NordVPN GUI, and
the graphics/SSL/SVG/clipboard dependencies discovered while configuring the
comparison VM. It can take several minutes. Package installation needs internet
before NordVPN sign-in. Setup output is `/var/log/playlite-vm-setup.log` inside
the guest; successful setup creates `/var/lib/playlite-vm/setup-complete`.

1. Sign into NordVPN using its desktop icon.
2. Click **Configure NordVPN after sign-in**. This enables firewall, routing,
   auto-connect, and kill switch; selects OpenVPN TCP; connects and verifies VPN
   protection. It then bootstraps Steam and installs LuaTools/LuaMoon in the
   background. Plugin **Set up isolated Steam** provides the same setup and
   upgrades the bridge in existing installer profiles.
3. Sign into Steam and LuaTools/providers when Steam opens. The launcher verifies
   NordVPN and its kill switch before starting Steam. Workshop subscriptions
   happen in Steam’s browser; Playlite’s Workshop panel prepares the selected
   game and opens its Workshop page.

The recipe bundles a checksum-verified, pinned LuaMoon installer and a bounded
Playlite command bridge. The installer installs LuaTools/LuaMoon after VPN sign-in
and Steam’s first update. Account state remains in the guest. No credentials are
bundled or copied from the host. The guest account auto-logs in, has no
password, and has passwordless sudo for local VM administration. No SSH port is
forwarded to it.

After setup, open the VM with the generated `open-vm.sh` in its userdata folder,
or run `bash install.sh open --root <userdata>`. Preview paths without changing
anything using `bash install.sh plan --shared <games-folder>`. Add `--no-start`
to create the VM without booting it.

References: [libvirt virtiofs](https://www.libvirt.org/kbase/virtiofs.html),
[Ubuntu cloud images](https://cloud-images.ubuntu.com/noble/current/), and
[NordVPN installation](https://support.nordvpn.com/hc/en-us/articles/20196094470929-How-to-install-the-NordVPN-app-on-Linux-distributions).
