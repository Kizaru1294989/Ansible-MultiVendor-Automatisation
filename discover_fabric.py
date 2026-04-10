#!/usr/bin/env python3
"""
discover_fabric.py
------------------
Decouverte automatique du fabric reseau via eAPI Arista :
  1. Scan TCP range IP mgmt (port 443)
  2. Connexion eAPI JSON-RPC sur chaque equipement
  3. Hostname + chassis MAC (show lldp local-info)
  4. Voisins LLDP (show lldp neighbors detail)
  5. Detection role depuis hostname (spine/leaf/host)
  6. Deduplication par chassis MAC
  7. Detection automatique :
     - eth_int_host   : port leaf connecte au host (via LLDP)
     - mlag_peer_eths : ports du peer-link MLAG (leaf <-> leaf meme paire)
  8. Matrice d'interconnexion spine <-> leaf
  9. Verification doublons MAC
  10. Generation vars_auto.json
"""

import ipaddress
import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("pip install requests")
    raise SystemExit(1)

# ─── CONSTANTES ───────────────────────────────────────────────────────────────

SCAN_TIMEOUT  = 2
SCAN_PORT     = 443
MAX_WORKERS   = 20
OUTPUT_FILE   = "vars_auto.json"

ROLE_PATTERNS = {
    "spine": re.compile(r"spine", re.IGNORECASE),
    "leaf":  re.compile(r"leaf",  re.IGNORECASE),
    "host":  re.compile(r"host",  re.IGNORECASE),
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def normalize_hostname(name: str) -> str:
    """
    Normalise le hostname pour Ansible :
      - Supprime le domaine FQDN
      - Lowercase
      - Retire les tirets et underscores
      ex: LEAF-3 -> leaf3 | spine_1 -> spine1 | leaf-5.domain.com -> leaf5
    """
    name = name.strip().split(".")[0].lower()
    name = re.sub(r"[-_]", "", name)
    return name

def detect_role(hostname: str) -> str:
    for role, pattern in ROLE_PATTERNS.items():
        if pattern.search(hostname):
            return role
    return "unknown"

def host_id(name: str) -> int:
    m = re.sub(r"[^0-9]", "", name)
    return int(m) if m else 0

def _parse_eth_id(intf: str) -> int:
    m = re.search(r"(\d+)", intf)
    return int(m.group(1)) if m else 0

# ─── SCAN RESEAU ──────────────────────────────────────────────────────────────

def parse_ip_range(range_str: str) -> list:
    range_str = range_str.strip()
    match = re.match(r"^(\d+\.\d+\.\d+\.)(\d+)-(\d+)$", range_str)
    if match:
        prefix = match.group(1)
        start  = int(match.group(2))
        end    = int(match.group(3))
        return [f"{prefix}{i}" for i in range(start, end + 1)]
    try:
        net = ipaddress.ip_network(range_str, strict=False)
        return [str(ip) for ip in net.hosts()]
    except ValueError:
        pass
    try:
        ipaddress.ip_address(range_str)
        return [range_str]
    except ValueError:
        raise ValueError(f"Format non reconnu : {range_str}")

def _tcp_ping(ip: str, port: int, timeout: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def scan_range(ip_list: list, port: int = SCAN_PORT) -> list:
    print(f"\nScan de {len(ip_list)} adresses sur le port {port}...")
    alive = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_tcp_ping, ip, port, SCAN_TIMEOUT): ip for ip in ip_list}
        for future in as_completed(futures):
            ip = futures[future]
            if future.result():
                alive.append(ip)
    alive.sort(key=lambda ip: [int(x) for x in ip.split(".")])
    print(f"  {len(alive)} equipement(s) joignables : {', '.join(alive)}")
    return alive

# ─── eAPI ─────────────────────────────────────────────────────────────────────

def eapi_call(ip: str, username: str, password: str, commands: list):
    url     = f"https://{ip}/command-api"
    payload = {
        "jsonrpc": "2.0",
        "method":  "runCmds",
        "params":  {"version": 1, "cmds": commands, "format": "json"},
        "id":      1,
    }
    try:
        resp = requests.post(
            url, json=payload,
            auth=(username, password),
            verify=False, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            print(f"  eAPI error {ip} : {data['error']['message']}")
            return None
        return data["result"]
    except requests.exceptions.ConnectTimeout:
        print(f"  Timeout : {ip}")
    except requests.exceptions.ConnectionError as e:
        print(f"  Connexion impossible : {ip} — {e}")
    except Exception as e:
        print(f"  Erreur inattendue {ip} : {e}")
    return None

# ─── COLLECTE PAR EQUIPEMENT ──────────────────────────────────────────────────

def collect_device_info(ip: str, username: str, password: str) -> dict:
    result = eapi_call(ip, username, password, [
        "show hostname",
        "show lldp local-info",
        "show lldp neighbors detail",
    ])
    if not result or len(result) < 3:
        return None

    hostname_raw = result[0].get("hostname", ip)
    hostname     = normalize_hostname(hostname_raw)
    role         = detect_role(hostname)
    chassis_mac  = result[1].get("chassisId", "").lower().strip()

    neighbors = []
    for local_intf, data in result[2].get("lldpNeighbors", {}).items():
        for nbr in data.get("lldpNeighborInfo", []):
            remote_hostname = normalize_hostname(nbr.get("systemName", ""))
            remote_mac      = nbr.get("chassisId", "").lower().strip()
            remote_intf     = nbr.get("neighborInterfaceInfo", {}).get("interfaceId", "")
            if not remote_hostname:
                continue
            neighbors.append({
                "local_intf":      local_intf,
                "local_eth":       _parse_eth_id(local_intf),
                "remote_hostname": remote_hostname,
                "remote_mac":      remote_mac,
                "remote_intf":     remote_intf,
                "remote_eth":      _parse_eth_id(remote_intf),
            })

    print(f"  {ip:18s} -> {hostname:12s} [{role}]  MAC={chassis_mac}  {len(neighbors)} voisin(s) LLDP")

    return {
        "ip":          ip,
        "hostname":    hostname,
        "role":        role,
        "chassis_mac": chassis_mac,
        "neighbors":   neighbors,
    }

# ─── COLLECTE PARALLELE ───────────────────────────────────────────────────────

def collect_all(alive_ips: list, username: str, password: str) -> dict:
    print(f"\nCollecte eAPI sur {len(alive_ips)} equipement(s)...")
    raw_devices = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(collect_device_info, ip, username, password): ip
            for ip in alive_ips
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                raw_devices.append(result)

    # Deduplication par chassis MAC
    devices   = {}
    mac_index = {}
    for d in raw_devices:
        mac = d["chassis_mac"]
        if mac and mac in mac_index:
            existing = devices[mac_index[mac]]
            if len(d["neighbors"]) > len(existing["neighbors"]):
                devices[mac_index[mac]] = d
        else:
            devices[d["hostname"]] = d
            if mac:
                mac_index[mac] = d["hostname"]

    return devices

# ─── VERIFICATION MACs ────────────────────────────────────────────────────────

def check_mac_duplicates(devices: dict) -> bool:
    print("\nVerification des MACs...")
    mac_map    = {}
    duplicates = False
    for hostname, info in devices.items():
        mac = info.get("chassis_mac", "")
        if not mac:
            print(f"  Avertissement : MAC inconnue pour {hostname}")
            continue
        if mac in mac_map:
            print(f"  DOUBLON MAC : {mac} sur {hostname} ET {mac_map[mac]}")
            duplicates = True
        else:
            mac_map[mac] = hostname
    if not duplicates:
        print("  Aucun doublon MAC detecte.")
    return not duplicates

# ─── RESOLUTION VOISINS ───────────────────────────────────────────────────────

def build_resolver(devices: dict):
    """Construit une fonction de resolution hostname via MAC ou nom."""
    mac_to_host = {
        d["chassis_mac"]: h
        for h, d in devices.items()
        if d["chassis_mac"]
    }

    def resolve(remote_hostname: str, remote_mac: str) -> str:
        if remote_mac and remote_mac in mac_to_host:
            return mac_to_host[remote_mac]
        if remote_hostname in devices:
            return remote_hostname
        clean = re.sub(r"[^a-z0-9]", "", remote_hostname)
        for known in devices:
            if re.sub(r"[^a-z0-9]", "", known) == clean:
                return known
        return remote_hostname

    return resolve

# ─── DETECTION AUTOMATIQUE PORTS ──────────────────────────────────────────────

def detect_mlag_peer_ports(devices: dict, resolve) -> dict:
    """
    Pour chaque paire MLAG (leaf1+leaf2, leaf3+leaf4...) :
    detecte les ports du peer-link via LLDP (leaf <-> leaf).
    Retourne { "leaf1": [3, 4], "leaf2": [3, 4], ... }
    """
    print("\nDetection des ports MLAG peer-link...")
    leafs = {h: d for h, d in devices.items() if d["role"] == "leaf"}
    mlag_ports = {}

    for hostname, data in sorted(leafs.items()):
        ports = []
        for nbr in data["neighbors"]:
            canonical = resolve(nbr["remote_hostname"], nbr["remote_mac"])
            # Un voisin leaf = port du peer-link MLAG
            if canonical in leafs and canonical != hostname:
                ports.append(nbr["local_eth"])
        if ports:
            ports.sort()
            mlag_ports[hostname] = ports
            print(f"  {hostname} : ports MLAG peer-link -> Eth{ports}")
        else:
            mlag_ports[hostname] = []

    return mlag_ports

def detect_host_ports(devices: dict, resolve) -> dict:
    """
    Pour chaque leaf : detecte les ports connectes aux hosts via LLDP.
    Retourne { "leaf1": [5], "leaf2": [5], ... }
    """
    print("\nDetection des ports host (eth_int_host)...")
    leafs = {h: d for h, d in devices.items() if d["role"] == "leaf"}
    hosts = {h: d for h, d in devices.items() if d["role"] == "host"}
    host_ports = {}

    for hostname, data in sorted(leafs.items()):
        ports = []
        for nbr in data["neighbors"]:
            canonical = resolve(nbr["remote_hostname"], nbr["remote_mac"])
            if canonical in hosts:
                ports.append(nbr["local_eth"])
        if ports:
            ports.sort()
            host_ports[hostname] = ports[0]  # premier port host
            print(f"  {hostname} : port host -> Eth{ports[0]}")
        else:
            host_ports[hostname] = None
            print(f"  {hostname} : aucun host detecte via LLDP")

    return host_ports

def detect_global_mlag_ports(mlag_ports: dict) -> tuple:
    """
    Depuis les ports peer-link de tous les leafs,
    deduit mlag_peer_first_eth_int et mlag_peer_second_eth_int globaux.
    Prend les ports les plus frequents.
    """
    from collections import Counter
    all_ports = []
    for ports in mlag_ports.values():
        all_ports.extend(ports)

    if not all_ports:
        return 3, 4  # valeurs par defaut

    counter = Counter(all_ports)
    most_common = [p for p, _ in counter.most_common(2)]
    most_common.sort()

    if len(most_common) >= 2:
        return most_common[0], most_common[1]
    elif len(most_common) == 1:
        return most_common[0], most_common[0] + 1
    return 3, 4

# ─── MATRICE D'INTERCONNEXION ─────────────────────────────────────────────────

def build_interconnect_links(devices: dict, resolve) -> list:
    print("\nConstruction de la matrice d'interconnexion...")

    spines = {h: d for h, d in devices.items() if d["role"] == "spine"}
    leafs  = {h: d for h, d in devices.items() if d["role"] == "leaf"}

    links = []
    seen  = set()

    for spine_name, spine_data in sorted(spines.items()):
        spine_id = host_id(spine_name)

        for nbr in spine_data["neighbors"]:
            canonical = resolve(nbr["remote_hostname"], nbr["remote_mac"])
            if canonical not in leafs:
                continue

            leaf_id = host_id(canonical)
            key     = (spine_id, leaf_id, nbr["local_eth"])
            if key in seen:
                continue
            seen.add(key)

            # Port du leaf vers ce spine
            leaf_eth = None
            for ln in leafs[canonical]["neighbors"]:
                if resolve(ln["remote_hostname"], ln["remote_mac"]) == spine_name:
                    leaf_eth = ln["local_eth"]
                    break

            if leaf_eth is None:
                print(f"  Avertissement : port leaf introuvable {spine_name} <-> {canonical}")
                continue

            link = {
                "spine":     spine_id,
                "leaf":      leaf_id,
                "spine_eth": nbr["local_eth"],
                "leaf_eth":  leaf_eth,
            }
            links.append(link)
            print(f"  spine{spine_id} Eth{nbr['local_eth']} <-> leaf{leaf_id} Eth{leaf_eth}")

    links.sort(key=lambda l: (l["spine"], l["leaf"]))
    return links

# ─── CONSTRUCTION vars_auto.json ──────────────────────────────────────────────

def build_vars_auto(devices: dict, links: list, mlag_ports: dict,
                    host_ports: dict, mlag_first: int, mlag_second: int,
                    fabric_defaults: dict, vxlan_evpn: dict,
                    arista_vars: dict) -> dict:

    spines = {h: d for h, d in devices.items() if d["role"] == "spine"}
    leafs  = {h: d for h, d in devices.items() if d["role"] == "leaf"}
    hosts  = {h: d for h, d in devices.items() if d["role"] == "host"}

    # MLAG global avec ports detectes automatiquement
    mlag = {
        "mgmt_gateway":             fabric_defaults.pop("mgmt_gateway", "192.168.28.254"),
        "mlag_vlan":                4094,
        "mlag_peer_name":           "lag",
        "mlag_netmask":             31,
        "mlag_channel_group":       4094,
        "trunk_group_name":         "mlag-peer",
        "mlag_peer_first_eth_int":  mlag_first,
        "mlag_peer_second_eth_int": mlag_second,
    }

    def spine_entry(d):
        return {"ansible_host": d["ip"], "mgmt_ip": d["ip"]}

    def leaf_entry(hostname, d):
        eth_host = host_ports.get(hostname)
        return {
            "ansible_host": d["ip"],
            "mgmt_ip":      d["ip"],
            # SVIs : l'user remplit vlan_id / ip / prefix / virtual_ip
            # eth_int_host est detecte automatiquement via LLDP
            "svis": [
                {
                    "vlan_id":    0,
                    "ip":         "",
                    "prefix":     24,
                    "virtual_ip": "",
                    "eth_int_host": eth_host if eth_host is not None else 5,
                }
            ],
        }

    def host_entry(d):
        return {
            "ansible_host":  d["ip"],
            "mgmt_ip":       d["ip"],
            "po_id":         10,
            "po_ip":         "",
            "eth_po_first":  1,
            "eth_po_second": 2,
            "route":         "",
        }

    return {
        "fabric":     fabric_defaults,
        "mlag":       mlag,
        "vxlan_evpn": vxlan_evpn,
        "inventory": {
            "arista_vars": arista_vars,
            "spines": {h: spine_entry(d) for h, d in sorted(spines.items())},
            "leafs":  {h: leaf_entry(h, d) for h, d in sorted(leafs.items())},
            "hosts":  {h: host_entry(d)  for h, d in sorted(hosts.items())},
        },
        "interconnect_links": links,
    }

def save_vars_auto(data: dict, path: str = OUTPUT_FILE):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  OK  {path} genere.")

# ─── DEFAULTS ─────────────────────────────────────────────────────────────────

DEFAULT_FABRIC = {
    "mgmt_gateway":             "192.168.28.254",
    "loopback0_base":           "172.16.0.0/24",
    "loopback1_base":           "1.1.2.0/24",
    "loopback_test_base":       "10.10.10.0/24",
    "mlag_spine_base":          "172.16.5.0/31",
    "mlag_leaf_base":           "100.0.0.0/24",
    "interconnect_base":        "172.16.200.0/24",
    "interconnect_prefix":      31,
    "asn_spines":               65000,
    "asn_leafs_base":           65001,
    "vni_base":                 10000,
    "route_distinguisher_base": 1000,
    "loopback_test_id":         100,
    "virtual_router_mac_base":  "00:1c:73:00:00:",
    "bgp_max_paths":            4,
    "bgp_max_routes":           12000,
}

DEFAULT_VXLAN_EVPN = {
    "peer_group":         "SPINE",
    "peer_group_evpn":    "SPINE-EVPN-TRANSIT",
    "vrf_customer_names": "customer",
}

# ─── POINT D'ENTREE ───────────────────────────────────────────────────────────

def run_discovery(username: str, password: str, ip_range: str) -> dict:
    # 1. Scan
    ip_list   = parse_ip_range(ip_range)
    alive_ips = scan_range(ip_list)
    if not alive_ips:
        print("Aucun equipement joignable.")
        raise SystemExit(1)

    # 2. Collecte eAPI
    devices = collect_all(alive_ips, username, password)
    if not devices:
        print("Aucun equipement accessible.")
        raise SystemExit(1)

    # 3. Verification MACs
    check_mac_duplicates(devices)

    # 4. Resolver MAC -> hostname
    resolve = build_resolver(devices)

    # 5. Detection automatique des ports
    mlag_ports            = detect_mlag_peer_ports(devices, resolve)
    host_ports            = detect_host_ports(devices, resolve)
    mlag_first, mlag_second = detect_global_mlag_ports(mlag_ports)

    print(f"\n  Ports MLAG peer-link globaux detectes : Eth{mlag_first} / Eth{mlag_second}")

    # 6. Matrice d'interconnexion
    links = build_interconnect_links(devices, resolve)

    # Resume
    spines  = sorted(h for h, d in devices.items() if d["role"] == "spine")
    leafs   = sorted(h for h, d in devices.items() if d["role"] == "leaf")
    hosts   = sorted(h for h, d in devices.items() if d["role"] == "host")
    unknown = sorted(h for h, d in devices.items() if d["role"] == "unknown")

    print(f"\nResume :")
    print(f"  Spines  : {', '.join(spines)  or 'aucun'}")
    print(f"  Leafs   : {', '.join(leafs)   or 'aucun'}")
    print(f"  Hosts   : {', '.join(hosts)   or 'aucun'}")
    if unknown:
        print(f"  Inconnus: {', '.join(unknown)}")
    print(f"  Liens   : {len(links)} interconnexion(s) detectee(s)")

    # 7. Construction vars_auto
    import copy
    fabric = copy.deepcopy(DEFAULT_FABRIC)

    arista_vars = {
        "ansible_user":                   username,
        "ansible_password":               password,
        "ansible_connection":             "httpapi",
        "ansible_network_os":             "eos",
        "ansible_httpapi_use_ssl":        True,
        "ansible_httpapi_validate_certs": False,
        "ansible_httpapi_port":           443,
    }

    vars_auto = build_vars_auto(
        devices, links, mlag_ports, host_ports,
        mlag_first, mlag_second,
        fabric, DEFAULT_VXLAN_EVPN, arista_vars
    )

    # 8. Sauvegarde
    save_vars_auto(vars_auto)

    print(f"\n  Action requise : completez les SVIs dans vars_auto.json")
    print(f"  (vlan_id, ip, prefix, virtual_ip) pour chaque leaf")
    print(f"  puis relancez : python3 main.py --generate")

    return vars_auto