"""Storage ownership and VPN gates for the standalone VM installer."""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET


def load_script(name, filename):
    path = Path(__file__).resolve().parents[1] / 'tools/vm' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_script('vm_installer', 'install.py')
guest = load_script('vm_guest', 'guest_control.py')


class VMInstallerTests(unittest.TestCase):
    def test_service_registration_links_profile_unit_to_user_manager(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'virtiofs.service').write_text('[Service]\nExecStart=/bin/true\n')
            cfg={'root':str(root),'service':'playlite-steam-test','socket':'/test.sock'}
            with patch.object(installer,'execute') as execute:
                installer.register_service(cfg)
            unit=root/'playlite-steam-test.service'
            self.assertEqual(unit.read_text(),(root/'virtiofs.service').read_text())
            self.assertEqual(execute.call_args_list[0].args[0],['systemctl','--user','link','--force',unit])

    def test_command_failure_includes_underlying_stderr(self):
        error=subprocess.CalledProcessError(5,['systemctl'],stderr='Unit could not be found.')
        with patch.object(installer.subprocess,'run',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'Unit could not be found'):
                installer.execute(['systemctl','--user','start','missing.service'])

    def config(self, root):
        with patch.object(installer, 'shared_mount', return_value={'target': '/pool', 'fstype': 'fuse.mergerfs'}):
            return installer.configuration(Path(root) / 'userdata', root)

    def test_private_vm_folder_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'separate'):
                installer.configuration(Path(root) / 'vm', root)

    def test_generated_vm_uses_external_virtiofs_and_local_spice(self):
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / 'games'; shared.mkdir()
            with patch.object(installer, 'shared_mount', return_value={'target': '/pool', 'fstype': 'fuse.mergerfs'}):
                cfg = installer.configuration(Path(root) / 'userdata', shared)
            xml = ET.fromstring(installer.domain_xml(cfg))
            self.assertEqual(xml.find('./devices/filesystem/driver').get('type'), 'virtiofs')
            self.assertEqual(xml.find('./devices/filesystem/source').get('socket'), cfg['socket'])
            self.assertEqual(xml.find('./memoryBacking/access').get('mode'), 'shared')
            self.assertEqual(xml.find('./devices/graphics/listen').get('address'), '127.0.0.1')
            self.assertEqual(xml.find('./devices/graphics/clipboard').get('copypaste'), 'yes')
            self.assertEqual(xml.find('./devices/graphics/filetransfer').get('enable'), 'no')
            self.assertIsNotNone(xml.find('./devices/disk/backingStore/source'))

    def test_download_share_exports_only_dedicated_userdata_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / 'games'; shared.mkdir()
            with patch.object(installer, 'shared_mount', return_value={'target': '/pool', 'fstype': 'fuse.mergerfs'}):
                cfg = installer.configuration(Path(temporary) / 'userdata', shared)
            xml = ET.fromstring(installer.domain_xml(cfg))
            shares = xml.findall('./devices/filesystem')
            self.assertEqual([share.find('target').get('dir') for share in shares], ['standalone-games', 'steam-downloads'])
            downloads = installer.download_config(cfg)
            self.assertEqual(downloads['shared'], str(Path(cfg['root']) / 'download-storage'))
            self.assertNotEqual(downloads['socket'], cfg['socket'])
            self.assertEqual(shares[1].find('source').get('socket'), downloads['socket'])
            self.assertIn(['steam-downloads', '/mnt/playlite-downloads', 'virtiofs', 'defaults,nofail', '0', '0'], installer.cloud_config(cfg)['mounts'])
            self.assertEqual(len(installer.storage_services(cfg)), 2)
            self.assertIn(downloads['shared'], installer.service_text(downloads, '/virtiofsd', 100000, 100000))

    def test_download_mount_failure_does_not_create_local_fallback(self):
        storage = load_script('vm_storage', 'prepare-storage.py')
        with patch.object(storage.os.path, 'ismount', side_effect=[True, False]), \
             patch.object(storage.Path, 'read_text', return_value=json.dumps({'staging': '.steam-vm-123456789abc', 'download_storage': True})), \
             patch.object(storage.Path, 'mkdir') as mkdir:
            with self.assertRaisesRegex(RuntimeError, 'download storage is not mounted'):
                storage.prepare()
            mkdir.assert_not_called()

    def test_seed_has_no_password_keys_or_copied_session(self):
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / 'games'; shared.mkdir()
            with patch.object(installer, 'shared_mount', return_value={'target': '/pool', 'fstype': 'fuse.mergerfs'}):
                cfg = installer.configuration(Path(root) / 'userdata', shared)
            cloud = installer.cloud_config(cfg)
            user = cloud['users'][0]
            self.assertTrue(user['lock_passwd'])
            self.assertNotIn('passwd', user)
            self.assertNotIn('ssh_authorized_keys', user)
            self.assertFalse(cloud['ssh_pwauth'])
            self.assertEqual(cloud['mounts'][0][2], 'virtiofs')
            files={file['path'] for file in cloud['write_files']}
            self.assertIn('/usr/local/lib/playlite-vm/steam/setup_moon.py',files)
            self.assertIn('/usr/local/lib/playlite-vm/runtime_server.py',files)
            wine_setup = next(file for file in cloud['write_files']
                              if file['path'] == '/usr/local/lib/playlite-vm/install-steam-auto-crack.sh')
            self.assertEqual(wine_setup['permissions'], '0755')
            self.assertIn('runuser -u ubuntu', wine_setup['content'])
            self.assertIn('install-steam-auto-crack.sh', installer.ASSET_FILES)
            self.assertNotIn('/home/ubuntu/.steam/steam/config/loginusers.vdf',files)

    def test_missing_merged_mount_never_starts_vm(self):
        cfg = {'shared': '/games', 'shared_mount': {'target': '/pool', 'fstype': 'fuse.mergerfs'}}
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(installer, 'shared_mount', return_value={'target': '/', 'fstype': 'ext4'}), \
             patch.object(installer, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'fallback'):
                installer.open_vm(cfg)
            execute.assert_not_called()

    def test_root_mapping_handles_mergerfs_private_directories(self):
        cfg = {'shared': '/pool/games', 'socket': '/run/user/1000/test.sock', 'uid': 1000, 'gid': 1000}
        text = installer.service_text(cfg, '/usr/libexec/virtiofsd', 100000, 100000)
        self.assertIn(':0:1000:1:', text)
        self.assertIn('map:1000:0:1', text)
        self.assertNotIn('--sandbox none', text)


class VPNGateTests(unittest.TestCase):
    def test_steam_never_starts_without_share(self):
        with patch.object(guest.os.path, 'ismount', return_value=False), patch.object(guest, 'launch') as launch:
            with self.assertRaisesRegex(RuntimeError, 'filesystem'):
                guest.dispatch('start-steam')
            launch.assert_not_called()

    def test_connected_vpn_without_kill_switch_is_rejected(self):
        with patch.object(guest, 'run', side_effect=[Mock(stdout='Status: Connected'), Mock(stdout='Kill Switch: disabled')]):
            with self.assertRaisesRegex(RuntimeError, 'kill switch'):
                guest.vpn_ready()

    def test_unprotected_connection_is_rejected(self):
        with patch.object(guest, 'run', side_effect=[Mock(stdout='Status: Connected'),
             Mock(stdout='Kill Switch: enabled'), Mock(returncode=0, stdout='{"protected":false}')]):
            with self.assertRaisesRegex(RuntimeError, 'protection'):
                guest.vpn_ready()


if __name__ == '__main__':
    unittest.main()

class HostEnvironmentTests(unittest.TestCase):
    def test_repo_libvirt_uses_normal_home(self):
        import os
        from unittest.mock import patch
        from downloader.vm_backend import installer_module
        with patch.dict(os.environ,{'PLAYLITE_PROFILE':'repo','HOME':'/long/repo/home','PLAYLITE_HOST_HOME':'/home/user','XDG_CONFIG_HOME':'/long/repo/config','PLAYLITE_HOST_XDG_CONFIG_HOME':''},clear=True):
            env=installer_module().host_environment()
            self.assertEqual(env['HOME'],'/home/user')
            self.assertNotIn('XDG_CONFIG_HOME',env)
