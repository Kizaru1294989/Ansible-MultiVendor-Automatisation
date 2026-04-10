#!/usr/bin/env python3
"""
generate_vars_auto.py
---------------------
Lit vars_auto.json et calcule AUTOMATIQUEMENT toutes les variables reseau.

Calcule aussi automatiquement depuis les SVIs :
  - bgp_networks : reseau de chaque SVI + loopback1 partage de la paire MLAG
  - loopback partagé de la paire MLAG (loopback1_ip)

L'user ne renseigne dans vars_auto.json que :
  - vlan_id, ip, prefix, virtual_ip  (par SVI)
  - eth_int_host est detecte automatiquement par discover_fabric.py via LLDP
"""

import ipaddress
import json
import os
import re
import yaml

# ─── CONFIG ───────────────────────────────────────────────────────────────────

VARS_FILE   = "vars_auto.json"
OUTPUT_BASE = "inventories/production"
GROUP_VARS  = os.path.join(OUTPUT_BASE, "group_vars", "all")
HOST_VARS   = os.path.join(OUTPUT_BASE, "host_vars")
HOSTS_FILE  = os.path.join(OUTPUT_BASE, "hosts")

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def write_yaml(path: str, data: dict, header: str = ""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if header:
            f.write(f"# {header}\n\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  OK  {path}")

def load_vars(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)

def host_id(name: str) -> int:
    m = re.sub(r"[^0-9]", "", name)
    return int(m) if m else 0

def nth_ip(network: str, n: int) -> str:
    net = ipaddress.ip_network(network, strict=False)
    return str(net[n])

def nth_subnet(base: str, prefix: int, n: int) -> ipaddress.IPv4Network:
    base_net = ipaddress.ip_network(base, strict=False)
    for i, s in enumerate(base_net.subnets(new_prefix=prefix)):
        if i == n:
            return s
    raise ValueError(f"Pas assez de sous-reseaux dans {base} pour index {n}")

def svi_network(ip: str, prefix: int) -> str:
    """Calcule le reseau d'une SVI. ex: 172.16.115.2/24 -> 172.16.115.0/24"""
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return str(net)

# ─── CALCUL bgp_networks AUTOMATIQUE ─────────────────────────────────────────

def compute_bgp_networks(svis: list, loopback1_ip: str, loopback_shared: str) -> list:
    """
    bgp_networks = reseau de chaque SVI + loopback1 partage de la paire MLAG /32.
    """
    networks = []

    # Reseau de chaque SVI
    for svi in svis:
        ip     = svi.get("ip", "")
        prefix = svi.get("prefix", 24)
        if ip:
            net = svi_network(ip, prefix)
            if net not in networks:
                networks.append(net)

    # Loopback1 partage de la paire MLAG en /32
    if loopback_shared:
        lb = f"{loopback_shared}/32"
        if lb not in networks:
            networks.append(lb)

    return networks

# ─── CALCULS AUTOMATIQUES ─────────────────────────────────────────────────────

def compute_all(data: dict) -> dict:
    fab  = data["fabric"]
    inv  = data["inventory"]

    spines_inv = inv["spines"]
    leafs_inv  = inv["leafs"]
    hosts_inv  = inv["hosts"]
    links      = data["interconnect_links"]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def leaf_pair(leaf_idx: int) -> int:
        """Index de la paire MLAG (0-indexe). leaf1+leaf2 -> 0, leaf3+leaf4 -> 1"""
        return (leaf_idx - 1) // 2

    def leaf_asn(leaf_idx: int) -> int:
        return fab["asn_leafs_base"] + leaf_pair(leaf_idx)

    def leaf_mlag_ips(leaf_idx: int):
        subnet       = nth_subnet(fab["mlag_leaf_base"], 31, leaf_pair(leaf_idx))
        pos          = (leaf_idx - 1) % 2
        return str(subnet[pos]), str(subnet[1 - pos])

    def leaf_vni(leaf_idx: int) -> int:
        return fab["vni_base"] + (leaf_pair(leaf_idx) + 1) * 3

    def leaf_rd(leaf_idx: int) -> int:
        return fab["route_distinguisher_base"] + leaf_pair(leaf_idx) * 1000

    def leaf_mac(leaf_idx: int) -> str:
        pair = leaf_pair(leaf_idx)
        return f"{fab['virtual_router_mac_base']}{(pair + 1) * 12:02x}"

    def leaf_loopback1(leaf_idx: int) -> str:
        """Loopback1 partage par la paire MLAG."""
        return nth_ip(fab["loopback1_base"], leaf_pair(leaf_idx) + 1)

    # ── Interconnects ─────────────────────────────────────────────────────────

    prefix   = fab["interconnect_prefix"]
    base_int = int(ipaddress.ip_network(fab["interconnect_base"], strict=False).network_address)
    step     = 2 ** (32 - prefix)

    interconnects = []
    leafs_asn     = {}

    print("\n  Calcul des sous-reseaux d'interconnexion :")
    for i, link in enumerate(links):
        subnet   = ipaddress.ip_network(f"{ipaddress.ip_address(base_int + i * step)}/{prefix}")
        spine_ip = str(subnet[0])
        leaf_ip  = str(subnet[1])
        network  = str(subnet)

        interconnects.append({
            "spine":     link["spine"],
            "leaf":      link["leaf"],
            "network":   network,
            "spine_ip":  spine_ip,
            "leaf_ip":   leaf_ip,
            "spine_eth": link["spine_eth"],
            "leaf_eth":  link["leaf_eth"],
        })

        leafs_asn[link["leaf"]] = leaf_asn(link["leaf"])
        print(f"    spine{link['spine']} <-> leaf{link['leaf']} : {network}  spine={spine_ip}  leaf={leaf_ip}")

    # ── host_vars spines ──────────────────────────────────────────────────────

    computed_spines = {}
    for idx, (name, inv_data) in enumerate(sorted(spines_inv.items()), start=1):
        s_id        = host_id(name)
        mlag_subnet = ipaddress.ip_network(fab["mlag_spine_base"], strict=False)
        mlag_ip     = str(mlag_subnet[idx - 1])
        mlag_peer   = str(mlag_subnet[1 - (idx - 1)])

        computed_spines[name] = {
            "loopback0_ip": nth_ip(fab["loopback0_base"], s_id),
            "bgp_asn":      fab["asn_spines"],
            "mlag_ip":      mlag_ip,
            "mlag_peer_ip": mlag_peer,
        }

    # ── host_vars leafs ───────────────────────────────────────────────────────

    computed_leafs = {}
    for name, inv_data in sorted(leafs_inv.items()):
        l_id               = host_id(name)
        mlag_ip, mlag_peer = leaf_mlag_ips(l_id)
        loopback1          = leaf_loopback1(l_id)

        # SVIs depuis vars_auto.json (l'user a rempli vlan_id/ip/prefix/virtual_ip)
        svis = inv_data.get("svis", [])

        # bgp_networks calcule automatiquement depuis les SVIs + loopback1
        loopback_shared = f"{nth_ip(fab['loopback0_base'], 10 + l_id)}"
        bgp_networks    = compute_bgp_networks(svis, loopback1, loopback_shared)

        computed_leafs[name] = {
            "loopback0_ip":        nth_ip(fab["loopback0_base"], 10 + l_id),
            "bgp_asn":             leaf_asn(l_id),
            "mlag_ip":             mlag_ip,
            "mlag_peer_ip":        mlag_peer,
            "virtual_router_mac":  leaf_mac(l_id),
            "loopback1_ip":        loopback1,
            "loopback_test_id":    fab["loopback_test_id"],
            "loopback_test_ip":    nth_ip(fab["loopback_test_base"], l_id * 10),
            "route_distinguisher": leaf_rd(l_id),
            "vni_l2":              leaf_vni(l_id),
            "bgp_networks":        bgp_networks,
            "svis":                svis,
        }

    # ── host_vars hosts ───────────────────────────────────────────────────────

    computed_hosts = {}
    for name, inv_data in sorted(hosts_inv.items()):
        computed_hosts[name] = {
            "po_id":         inv_data.get("po_id", 10),
            "po_ip":         inv_data.get("po_ip", ""),
            "eth_po_first":  inv_data.get("eth_po_first", 1),
            "eth_po_second": inv_data.get("eth_po_second", 2),
            "route":         inv_data.get("route", ""),
        }

    return {
        "spines":        computed_spines,
        "leafs":         computed_leafs,
        "hosts":         computed_hosts,
        "interconnects": interconnects,
        "leafs_asn":     leafs_asn,
    }

# ─── GENERATEURS ──────────────────────────────────────────────────────────────

def generate_group_vars(data: dict, computed: dict):
    print("\ngroup_vars/all/")

    # mlag.yml — directement depuis vars_auto.json (ports detectes automatiquement)
    write_yaml(
        os.path.join(GROUP_VARS, "mlag.yml"),
        data["mlag"],
        "MLAG - Variables globales (ports peer-link detectes via LLDP)"
    )

    # bgp.yml
    bgp = {
        "bgp_max_paths":  data["fabric"].get("bgp_max_paths", 4),
        "bgp_max_routes": data["fabric"].get("bgp_max_routes", 12000),
        "bgp_asn_spines": data["fabric"]["asn_spines"],
        "leafs_asn":      computed["leafs_asn"],
        "interconnects":  computed["interconnects"],
    }
    write_yaml(
        os.path.join(GROUP_VARS, "bgp.yml"),
        bgp,
        "BGP - Variables globales + interconnects (generes automatiquement)"
    )

    # vxlan_evpn.yml
    write_yaml(
        os.path.join(GROUP_VARS, "vxlan_evpn.yml"),
        data["vxlan_evpn"],
        "VXLAN EVPN - Variables globales communes a tous les leafs"
    )

def generate_host_vars(computed: dict):
    print("\nhost_vars/")
    all_hosts = {**computed["spines"], **computed["leafs"], **computed["hosts"]}
    for hostname, vars_ in sorted(all_hosts.items()):
        write_yaml(
            os.path.join(HOST_VARS, f"{hostname}.yml"),
            vars_,
            f"Variables specifiques a {hostname} (generees automatiquement)"
        )

def generate_hosts_file(data: dict):
    print("\nhosts")
    inv    = data["inventory"]
    av     = inv["arista_vars"]
    groups = {
        "spines": inv["spines"],
        "leafs":  inv["leafs"],
        "hosts":  inv["hosts"],
    }

    os.makedirs(OUTPUT_BASE, exist_ok=True)
    lines = []

    for group_name, members in groups.items():
        lines.append(f"[{group_name}]")
        for hostname, hvars in sorted(members.items()):
            lines.append(f"{hostname} ansible_host={hvars['ansible_host']} mgmt_ip={hvars['mgmt_ip']}")
        lines.append("")

    lines.append("[arista:children]")
    for group_name in groups:
        lines.append(group_name)
    lines.append("")

    lines.append("[arista:vars]")
    for k, v in av.items():
        lines.append(f"{k}={str(v).lower() if isinstance(v, bool) else v}")

    with open(HOSTS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  OK  {HOSTS_FILE}")

# ─── POINT D'ENTREE ───────────────────────────────────────────────────────────

def run_generation(vars_auto: dict = None):
    if vars_auto is None:
        if not os.path.exists(VARS_FILE):
            print(f"Fichier introuvable : {VARS_FILE}")
            raise SystemExit(1)
        vars_auto = load_vars(VARS_FILE)

    print("\nGeneration des fichiers Ansible...")
    computed = compute_all(vars_auto)
    generate_group_vars(vars_auto, computed)
    generate_host_vars(computed)
    generate_hosts_file(vars_auto)
    print(f"\nTous les fichiers generes dans : {OUTPUT_BASE}/")

if __name__ == "__main__":
    run_generation()