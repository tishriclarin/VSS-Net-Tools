# Debian 13 + MikroTik native Back to Home

This tool generates a new client profile without resetting existing BTH users, keys, LANs, VLANs or WAN routing.

The CCR is behind NAT. A standalone ordinary WireGuard peer cannot replace BTH's relay registration. Debian therefore creates a client key first; MikroTik registers that key as a native BTH user and supplies the actual relay-aware profile. Debian then validates that profile and writes its final configuration. No endpoint or router key is guessed.

| Role | Default |
|---|---|
| Router label (does not rename its identity) | `bongao-catv_core-router` |
| Router loopback accessed through BTH | `10.231.1.10/32` |
| Debian VPN client | `192.168.216.10/32` |
| BTH user | `debian-bongao` |
| Debian interface | `bongao` |
| Final Debian configuration | `/etc/wireguard/bongao.conf` |

Reserve `10.231.0.0/20` for this site in your address plan; only the router's `/32` is configured here. No VLAN 4090 is needed. This is remote management of one CCR, not a site-to-site routing setup.

## 1. Install dependencies on Debian 13

Download `bth_setup.py` into your working directory, then run:

```bash
sudo apt update
sudo apt install python3 wireguard-tools
sudo python3 ./bth_setup.py init
```

The generator now prompts for:

```text
BTH access IP (router loopback) [10.231.1.10]:
Hostname [bongao-catv_core-router]:
Short description:
```

The BTH access IP is the router's dedicated loopback, not the Debian client's VPN address. Hostname and description are registry metadata included in the generated file comments; they do not rename the live router.

The IP is checked immediately after entry. If it already exists in SQLite, generation stops before creating keys or files and shows the existing hostname/description plus an `edit --name ...` suggestion. Profile names and hostnames are unique too; hostnames are compared without regard to case. Duplicate checks are repeated inside a database write transaction to prevent concurrent generation from claiming the same IP.

For unattended generation, supply all three values:

```bash
sudo python3 ./bth_setup.py init --name bongao \
  --router-ip 10.231.1.10 \
  --hostname bongao-catv_core-router \
  --description "Bongao CATV core router"
```

This creates the directory `/etc/wireguard/.bth-bongao/` with permissions 0700 and files with permissions 0600:

- `state.json`: the client key and chosen parameters.
- `client-interface.pending`: a partial client configuration, deliberately not an active `.conf`.
- `01-mikrotik-bth.rsc`: native BTH user registration, including the new client private key.
- `02-mikrotik-firewall.rsc`: suggested router loopback and firewall configuration.
- `03-mikrotik-rollback.rsc`: removes the changes created for this profile.

The shared SQLite registry is `/etc/wireguard/bth-registry.sqlite3`. It stores hostname, description, router and client IPs, profile name, timestamps, generation status, client key information, generated RouterOS files and, after `finish`, the final Debian configuration. It is mode 0600 and contains private keys in plaintext; keep the database and its backups private. The normal list/show commands never display keys.

The script never prints private keys and does not start a tunnel. The provisioning file necessarily contains the client private key because it registers that key with native BTH. Transfer it privately over your existing trusted management connection. Keep state and exported profiles private; do not paste them into chats or tickets.

If you already used `bongao.conf` or the previous client address, the script or RouterOS checks will stop instead of overwriting existing access. Select a new name and an UNUSED address in the router's current BTH subnet, for example:

```bash
sudo python3 ./bth_setup.py init --name bongao-new --client-ip 192.168.216.20
```

Use the same `--name` for the finish step. Each router needs a separate profile. Unique router loopbacks alone do not turn multiple BTH tunnels into an interconnected network.

Always use the same `--directory` for a shared inventory. Its default is `/etc/wireguard`; changing it selects a different SQLite registry, so duplicate checks cannot see records in other directories or on other Debian machines. The check is an inventory lookup, not a network discovery or ping test.

Older generated files from a version without SQLite are preserved, not silently enrolled or overwritten. To start a new registered profile alongside them, use a new `--name` and an unused BTH client IP on the router.

## SQLite inventory and editing

List registered systems or show one record:

```bash
sudo python3 ./bth_setup.py list
sudo python3 ./bth_setup.py show --name bongao
```

If the IP already exists, the generator returns a nonzero exit status and an explanation such as:

```text
BTH IP already registered: 10.231.1.10 | bongao-catv_core-router | Bongao CATV core router
No configuration was generated. Edit the existing entry with:
  sudo python3 ./bth_setup.py edit --name bongao
```

Edit an existing record interactively:

```bash
sudo python3 ./bth_setup.py edit --name bongao
```

Press Enter to retain each existing value. Editing preserves the client key and updates the registry, state and generated RouterOS files. It refuses duplicate IPs/hostnames and refuses to overwrite generated files that were modified manually outside this tool.

Before `finish`, the BTH access IP can be edited. If you already applied the RouterOS files, changing local files does not change the live CCR: coordinate the migration separately instead of reimporting creation scripts. After `finish`, the record becomes `ready`; the BTH IP is locked against in-place changes, while hostname and description can still be edited. Existing live Debian configurations and running tunnels are never rewritten by `edit`.

Use `show` or `list` for the `generated`/`ready` status. These describe local generation, not whether the router configuration has been applied or a tunnel is currently connected.

## 2. Register the client on the CCR

For a local desktop upload using your normal user account, create private transfer copies (from a new working directory):

```bash
mkdir -m 700 bth-transfer
sudo install -m 600 -o "$(id -u)" -g "$(id -g)" /etc/wireguard/.bth-bongao/01-mikrotik-bth.rsc bth-transfer/
sudo install -m 600 -o "$(id -u)" -g "$(id -g)" /etc/wireguard/.bth-bongao/02-mikrotik-firewall.rsc bth-transfer/
```

Review the files locally, then upload them to the CCR using your existing management connection. Do not run `/system reset-configuration` or revoke all BTH users.

On the CCR (RouterOS 7.23.x):

```routeros
/import file-name=01-mikrotik-bth.rsc verbose=yes dry-run=yes
```

If clean:

```routeros
/import file-name=01-mikrotik-bth.rsc
```

The first script enables DDNS/BTH without revoking existing users, and creates `debian-bongao` with the Debian-generated client key. Run it only once. It refuses a client IP that overlaps an existing WireGuard peer's allowed range.

The dry run can display the provisioning line containing the client key. Keep that terminal and output private. Once registration succeeds, delete the private provisioning upload from the CCR Files window and delete the transfer copy when no longer needed.

## 3. Review and apply the firewall suggestion

The second script adds:

- An empty bridge as a loopback with `10.231.1.10/32`, or reuses an existing exact `/32` on an empty bridge.
- A whitelist for `192.168.216.10/32`.
- A destination-specific input chain allowing TCP 22/8291 and ICMP only from that whitelist through `back-to-home-vpn`.
- A drop for all other access to the new loopback.
- A jump placed before the existing static firewall rules so older broad static management allows do not bypass the new address's whitelist.

Existing dynamic rules remain before that jump. In the reference CCR configuration, BTH's generated rule permits its UDP transport. Before applying to a different router, verify no dynamic input rule broadly permits access to the loopback.

```routeros
/ip firewall filter print detail where dynamic=yes
/import file-name=02-mikrotik-firewall.rsc verbose=yes dry-run=yes
```

If clean and the suggestion matches your router:

```routeros
/import file-name=02-mikrotik-firewall.rsc
```

The script checks the existing SSH/WinBox ports, source bindings and VRF. It does not discard restrictive bindings. It enables those services after the new loopback firewall is installed. Other router addresses retain their previous access rules; this is not a full firewall replacement.

The BTH user has `allow-lan=no`: forwarding into internal LANs is not enabled. Access to the CCR itself is handled by the input whitelist. No new NAT, port forwarding, DNS, DHCP or default-route rules are needed for this access.

## 4. Export the NEW profile from MikroTik

Run privately:

```routeros
/interface wireguard peers show-client-config debian-bongao
```

Copy the plain profile only, from `[Interface]` through BOTH `[Peer]` sections, without the CLI prompt, QR code or screen truncation. It must be the new user created in step 2, not `/ip cloud`'s original default client profile.

On Debian, prepare a protected file and paste the profile into it:

```bash
sudo touch /etc/wireguard/.bth-bongao/router-profile.txt
sudo chmod 600 /etc/wireguard/.bth-bongao/router-profile.txt
sudo nano /etc/wireguard/.bth-bongao/router-profile.txt
```

## 5. Finish the Debian configuration

```bash
sudo python3 ./bth_setup.py finish --profile /etc/wireguard/.bth-bongao/router-profile.txt
```

The script checks the profile's client key and address, requires both BTH peers, and preserves their public keys, endpoints, preshared keys if present, and keepalives. It writes `/etc/wireguard/bongao.conf` as mode 0600 without overwriting an existing file, stores the final configuration in SQLite, and changes the record's status to `ready`.

It narrows the router peer's `AllowedIPs` to `10.231.1.10/32`, retains the auxiliary peer's `0.0.0.0/32`, and uses the client address as a `/32`. DNS overrides and IPv6 default routes are omitted. Your existing Debian Internet access and DNS remain unchanged. No IP forwarding or NAT is enabled on Debian.

Profiles containing shell hooks, duplicate fields, missing relay peers, wrong client keys, mismatched addresses or malformed endpoints are rejected. No imported profile commands are executed.

## 6. Connect and verify

```bash
sudo wg-quick up bongao
sudo wg show bongao
ip route get 10.231.1.10
ping -c 4 10.231.1.10
ssh admin@10.231.1.10
```

WinBox destination: `10.231.1.10:8291`. Use your actual MikroTik login if it is not `admin`.

Look for a recent handshake on the router peer and increasing RX/TX counters. The auxiliary peer is special-purpose and need not show an ordinary handshake. The router's relay status alone does not prove the Debian client's tunnel is connected.

On the CCR:

```routeros
:put [/ip cloud get vpn-status]
:put [/ip cloud get vpn-relay-ipv4-status]
/ip firewall filter print stats where chain="BTH-bongao-IN"
```

On failure:

```bash
sudo wg show bongao
ip route get 10.231.1.10
```

If the handshake works but the whitelist counters do not rise, check the selected client source address, route and BTH interface. If no handshake occurs, verify the exported endpoints and both peers, working DNS on Debian, and the CCR's relay state. Do not open WinBox to the WAN to troubleshoot.

## Disconnect, automatic startup, and rollback

Disconnect a manually started tunnel:

```bash
sudo wg-quick down bongao
```

After a successful manual test, hand management to systemd (do not start the same interface twice):

```bash
sudo wg-quick down bongao
sudo systemctl enable --now wg-quick@bongao
```

Stop a systemd-managed tunnel with:

```bash
sudo systemctl disable --now wg-quick@bongao
```

For RouterOS rollback, upload and import the generated `03-mikrotik-rollback.rsc` from LOCAL management. It disconnects this new client and removes this generator's rules and any loopback it created; it does not remove a reused loopback. BTH, DDNS and management services remain enabled to avoid disrupting existing access.

Retire obsolete BTH client profiles only after verifying their replacement. This generator does not silently delete existing users or previous configuration files.

## Verification limits and sources

Sixteen local tests cover key matching, profile parsing, file permissions, non-overwrite behavior, route narrowing, retained peer data, interactive prompts, SQLite storage, duplicate IPs/hostnames, concurrent duplicate allocation, editing without key rotation, and finalized-IP protection. Generated RouterOS scripts still require the device's dry-run check. No live CCR or BTH relay connection was available for end-to-end testing.

- [MikroTik native BTH, users, relay peers and client export](https://manual.mikrotik.com/docs/network-management/cloud/back-to-home/)
- [WireGuard wg-quick configuration and routes](https://git.zx2c4.com/wireguard-tools/about/src/man/wg-quick.8)
- [WireGuard key generation and peer configuration](https://git.zx2c4.com/wireguard-tools/about/src/man/wg.8)
- [Debian 13 wireguard-tools package](https://packages.debian.org/trixie/wireguard-tools)
