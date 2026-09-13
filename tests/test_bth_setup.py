import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bth-debian13' / 'bth_setup.py'
spec = importlib.util.spec_from_file_location('bth_setup', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        subprocess.run(['python3', str(SCRIPT), 'init', '--directory', str(self.directory), '--router-ip', '10.231.1.10', '--hostname', 'bongao-catv_core-router', '--description', 'Bongao CATV core router'],
                       capture_output=True, text=True, check=True)
        self.state = json.loads((self.directory / '.bth-bongao/state.json').read_text())
        self.router_pub = mod.wg('pubkey', mod.wg('genkey') + '\n')
        self.psk = mod.wg('genpsk')
        self.profile = f'''[Interface]
PrivateKey = {self.state['private_key']}
Address = 192.168.216.10/24, fc00::10/64
DNS = 8.8.8.8
[Peer]
PublicKey = //////////////////////////////////////////8=
AllowedIPs = 0.0.0.0/32
Endpoint = example.sn.mynetname.net:52866
PersistentKeepalive = 15
[Peer]
PublicKey = {self.router_pub}
PresharedKey = {self.psk}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = example.vpn.mynetname.net:52866
PersistentKeepalive = 15
'''

    def tearDown(self):
        self.temp.cleanup()

    def test_init_permissions_and_no_live_conf(self):
        self.assertFalse((self.directory / 'bongao.conf').exists())
        state_dir = self.directory / '.bth-bongao'
        self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
        for p in state_dir.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        rsc = (state_dir / '01-mikrotik-bth.rsc').read_text()
        self.assertIn(self.state['private_key'], rsc)
        self.assertEqual(self.state['public_key'], mod.wg('pubkey', self.state['private_key'] + '\n'))

    def test_full_cli_finish(self):
        profile = self.directory / 'profile.txt'
        profile.write_text(self.profile)
        result = subprocess.run(['python3', str(SCRIPT), 'finish', '--directory', str(self.directory),
                                 '--profile', str(profile)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.directory / 'bongao.conf'
        text = out.read_text()
        self.assertEqual(text.count('[Peer]'), 2)
        self.assertIn('AllowedIPs = 10.231.1.10/32', text)
        self.assertIn('AllowedIPs = 0.0.0.0/32', text)
        self.assertNotIn('0.0.0.0/0', text)
        self.assertNotIn('DNS =', text)
        self.assertNotIn('::/0', text)
        self.assertIn('Address = 192.168.216.10/32', text)
        self.assertIn(self.router_pub, text)
        self.assertIn(self.psk, text)
        self.assertIn('example.vpn.mynetname.net:52866', text)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.state['private_key'], result.stdout + result.stderr)

    def test_reject_wrong_key_and_address(self):
        with self.assertRaises(mod.SetupError):
            mod.finish_config(self.state, self.profile.replace(self.state['private_key'], mod.wg('genkey')))
        with self.assertRaises(mod.SetupError):
            mod.finish_config(self.state, self.profile.replace('192.168.216.10/24', '192.168.216.2/24'))

    def test_reject_hooks_and_truncation(self):
        with self.assertRaises(mod.SetupError):
            mod.finish_config(self.state, self.profile.replace('[Peer]', 'PostUp = touch /tmp/unwanted\n[Peer]', 1))
        with self.assertRaises(mod.SetupError):
            mod.finish_config(self.state, self.profile.replace('example.vpn.mynetname.net:52866', 'example.vpn.mynetname.net:52>'))

    def test_reject_missing_relay_and_duplicate_field(self):
        interface, aux, main = self.profile.split('[Peer]')
        with self.assertRaises(mod.SetupError):
            mod.finish_config(self.state, interface + '[Peer]' + main)
        with self.assertRaises(mod.SetupError):
            mod.finish_config(self.state, self.profile.replace('MTU = 1420', '') + 'Endpoint = other.example:1234\n')

    def test_existing_state_is_not_rotated(self):
        old = (self.directory / '.bth-bongao/state.json').read_bytes()
        result = subprocess.run(['python3', str(SCRIPT), 'init', '--directory', str(self.directory), '--router-ip', '10.231.1.10', '--hostname', 'bongao-catv_core-router', '--description', 'Bongao CATV core router'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(old, (self.directory / '.bth-bongao/state.json').read_bytes())

    def test_no_overwrite_or_symlink_follow(self):
        out = self.directory / 'bongao.conf'
        out.write_text('existing config')
        profile = self.directory / 'profile.txt'
        profile.write_text(self.profile)
        result = subprocess.run(['python3', str(SCRIPT), 'finish', '--directory', str(self.directory),
                                 '--profile', str(profile)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out.read_text(), 'existing config')
        out.unlink()
        victim = self.directory / 'victim'
        victim.write_text('keep')
        out.symlink_to(victim)
        with self.assertRaises(FileExistsError):
            mod.write_new(out, 'bad')
        self.assertEqual(victim.read_text(), 'keep')

    def test_router_script_scope_and_address_protection_order(self):
        _, firewall, rollback = mod.templates(self.state)
        self.assertTrue(firewall.startswith('{\n'))
        self.assertTrue(firewall.endswith('}\n'))
        self.assertLess(firewall.index('chain=input action=jump'), firewall.index('/ip address add'))
        self.assertNotIn('/interface vlan add', firewall)
        self.assertNotIn('/ip firewall nat', firewall)
        self.assertNotIn('remove [find]', rollback)

    def cli(self, action, *args, input=None):
        return subprocess.run(['python3', str(SCRIPT), action, '--directory', str(self.directory), *args],
                              input=input, text=True, capture_output=True)

    def record(self, name='bongao'):
        with sqlite3.connect(self.directory / 'bth-registry.sqlite3') as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM routers WHERE name=?', (name,)).fetchone()
            return dict(row) if row else None

    def test_registry_stores_info_and_keeps_secrets_out_of_inventory(self):
        row = self.record()
        self.assertEqual(row['router_ip'], '10.231.1.10')
        self.assertEqual(row['hostname'], 'bongao-catv_core-router')
        self.assertEqual(row['description'], 'Bongao CATV core router')
        self.assertEqual(json.loads(row['state_json'])['private_key'], self.state['private_key'])
        self.assertTrue(json.loads(row['artifacts_json'])['01-mikrotik-bth.rsc'])
        self.assertEqual((self.directory / 'bth-registry.sqlite3').stat().st_mode & 0o777, 0o600)
        for action in ['list', 'show']:
            result = self.cli(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Bongao CATV core router', result.stdout)
            self.assertNotIn(self.state['private_key'], result.stdout + result.stderr)

    def test_interactive_prompts_create_registry_entry(self):
        result = self.cli('init', '--name', 'second', input='10.231.17.10\nsecond-core\nSecond site core\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ['BTH access IP', 'Hostname', 'Short description']:
            self.assertIn(expected, result.stdout)
        self.assertEqual(self.record('second')['description'], 'Second site core')

    def test_duplicate_ip_stops_before_other_prompts_and_suggests_edit(self):
        original = self.record()
        result = self.cli('init', '--name', 'second', input='10.231.1.10\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('edit --name bongao', result.stderr)
        self.assertIn('BTH IP already registered', result.stderr)
        self.assertNotIn('Hostname [', result.stdout)
        self.assertFalse((self.directory / '.bth-second').exists())
        self.assertEqual(self.record(), original)

    def test_edit_preserves_key_and_regenerates_files(self):
        result = self.cli('edit', '--router-ip', '10.231.2.10', '--hostname', 'bongao-core-updated',
                          '--description', "Bongao's updated core")
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.record()
        state = json.loads(row['state_json'])
        self.assertEqual(state['private_key'], self.state['private_key'])
        self.assertEqual(row['router_ip'], '10.231.2.10')
        self.assertEqual(row['description'], "Bongao's updated core")
        for filename, text in json.loads(row['artifacts_json']).items():
            path = self.directory / '.bth-bongao' / filename
            self.assertEqual(path.read_text(), text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        firewall = (self.directory / '.bth-bongao/02-mikrotik-firewall.rsc').read_text()
        self.assertIn('10.231.2.10/32', firewall)
        self.assertNotIn('10.231.1.10/32', firewall)

    def test_duplicate_hostname_and_edit_ip_are_rejected(self):
        result = self.cli('init', '--name', 'second', '--router-ip', '10.231.17.10',
                          '--hostname', 'BONGAO-CATV_CORE-ROUTER', '--description', 'Duplicate host')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Hostname already registered', result.stderr)
        result = self.cli('init', '--name', 'second', '--router-ip', '10.231.17.10',
                          '--hostname', 'second-core', '--description', 'Second site')
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.record('second')
        result = self.cli('edit', '--name', 'second', '--router-ip', '10.231.1.10',
                          '--hostname', 'second-core', '--description', 'Second site')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.record('second'), before)

    def test_final_config_saved_and_ready_ip_edit_refused(self):
        profile = self.directory / 'profile.txt'
        profile.write_text(self.profile)
        result = self.cli('finish', '--profile', str(profile))
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.record()
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['client_config'], (self.directory / 'bongao.conf').read_text())
        result = self.cli('edit', '--router-ip', '10.231.2.10', '--hostname', 'bongao-catv_core-router',
                          '--description', 'Changed')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot be changed after finalization', result.stderr)
        self.assertEqual(self.record(), row)
        result = self.cli('edit', '--router-ip', '10.231.1.10', '--hostname', 'bongao-new-label',
                          '--description', 'Updated metadata')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()['client_config'], row['client_config'])

    def test_edit_does_not_overwrite_external_changes(self):
        path = self.directory / '.bth-bongao/02-mikrotik-firewall.rsc'
        path.write_text(path.read_text() + '# manual change\n')
        before = self.record()
        result = self.cli('edit', '--router-ip', '10.231.1.10', '--hostname', 'bongao-catv_core-router',
                          '--description', 'New description')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('changed outside this tool', result.stderr)
        self.assertEqual(self.record(), before)
        self.assertTrue(path.read_text().endswith('# manual change\n'))

    def test_concurrent_duplicate_allocations_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        def create(name):
            return self.cli('init', '--name', name, '--router-ip', '10.231.33.10',
                            '--hostname', name + '-core', '--description', 'Concurrent allocation')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ['racer-a', 'racer-b']))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1])
        self.assertIn('already registered', next(r.stderr for r in results if r.returncode))
        self.assertEqual(sum((self.directory / ('.bth-' + n)).exists() for n in ['racer-a', 'racer-b']), 1)


if __name__ == '__main__':
    unittest.main()
