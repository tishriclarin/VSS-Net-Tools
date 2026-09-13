# VSS-Net-Tools

Network administration tools for VSS, starting with a Debian 13 generator for MikroTik's native Back to Home (BTH) feature.

## Public download

Download the generator on a Debian 13 machine:

```bash
sudo apt update
sudo apt install ca-certificates curl python3 wireguard-tools
curl --fail --location --output bth_setup.py \
  https://raw.githubusercontent.com/tishriclarin/VSS-Net-Tools/main/bth-debian13/bth_setup.py
python3 ./bth_setup.py --help
sudo python3 ./bth_setup.py init
```

The source is publicly downloadable without GitHub authentication. Execution is intended for Debian 13 with WireGuard installed, not arbitrary operating systems.

The interactive generator asks for the router's **BTH access IP**, **hostname**, and **short description**. It stores the records and generated configuration in a local SQLite registry, detects duplicate IPs, and suggests editing the existing record rather than replacing it.

```bash
sudo python3 ./bth_setup.py list
sudo python3 ./bth_setup.py show --name bongao
sudo python3 ./bth_setup.py edit --name bongao
```

## BTH workflow

1. Debian generates a client key and protected MikroTik provisioning files.
2. Apply the BTH registration file, then review and apply the suggested loopback/firewall file on the CCR.
3. Export the newly registered user's complete profile from MikroTik, including both generated peers.
4. Run the generator's `finish` command to create `/etc/wireguard/bongao.conf`.
5. Connect with `sudo wg-quick up bongao`.

Native BTH registration and MikroTik's generated relay profile are necessary for the intended router-behind-NAT setup. The generator never guesses relay endpoints or replaces BTH with an ordinary standalone WireGuard server.

See the [complete setup guide](bth-debian13/README.md) and [firewall preview](bth-debian13/firewall-preview.txt).

## Private runtime data

The public repository contains source code, documentation and tests only. Do not commit generated WireGuard profiles, RouterOS provisioning files, databases, keys or router exports.

Runtime files are kept locally under `/etc/wireguard`. The SQLite registry contains private keys and is created with mode 0600. Inventory `list` and `show` commands omit those secrets. The included `.gitignore` excludes common generated credential files.

## Tests

On a machine with Python 3 and `wireguard-tools` installed, from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The tests use temporary keys and directories and do not connect to a router. RouterOS imports still need device-side dry-run validation; live BTH connectivity has not been verified by these tests.
