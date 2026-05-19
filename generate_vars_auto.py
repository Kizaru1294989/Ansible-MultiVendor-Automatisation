#!/usr/bin/env python3
"""
generate_vars_auto.py
---------------------
Lit vars_auto_<dc>.json et calcule AUTOMATIQUEMENT toutes les variables reseau.

VNI par VLAN :
  - Chaque VLAN a son propre VNI = vni_base + vlan_id
  - ex: vlan 3 -> VNI 10003, vlan 6 -> VNI 10006

bgp_networks calcule automatiquement depuis les SVIs.
Multi-DC : genere dans inventories/<dc_name>/
"""

import ipaddress
import json
import os
import re
import yaml

# ─── CONFIG ───────────────────────────────────────────────────────────────────

VARS_FILE   = "vars_auto.json"   # defaut, overridable via run_generation(dc_name=...)

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
    """
    Extrait le numero d identifiant depuis un hostname.
    Prend le DERNIER groupe de chiffres trouve.
      ex: dc1-spine-1 -> 1 | leaf-3 -> 3 | dc2-leaf-12 -> 12
    """
    nums = re.findall(r"\d+", name)
    return int(nums[-1]) if nums else 0

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
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return str(net)

def clean_dci_links(dci_links: list) -> list:
    """
    Nettoie les dci_links avant ecriture dans host_vars :
    - Supprime les cles dont la valeur est None ou chaine vide ''
    - Evite que isn_loopback0: '' soit ecrit dans le YAML
      ce qui ferait generer un peer group BGP incomplet dans le J2
    """
    cleaned = []
    for link in dci_links:
        clean_link = {k: v for k, v in link.items() if v is not None and v != ""}
        cleaned.append(clean_link)
    return cleaned

# ─── CALCUL bgp_networks ──────────────────────────────────────────────────────

def compute_bgp_networks(svis: list, loopback0_ip: str) -> list:
    networks = []
    for svi in svis:
        ip     = svi.get("ip", "")
        prefix = svi.get("prefix", 24)
        if ip:
            net = svi_network(ip, prefix)
            if net not in networks:
                networks.append(net)
    lb = f"{loopback0_ip}/32"
    if lb not in networks:
        networks.append(lb)
    return networks

# ─── ENRICHISSEMENT SVIs avec VNI par VLAN ────────────────────────────────────

def enrich_svis(svis: list, vni_base: int) -> list:
    enriched = []
    for svi in svis:
        s = dict(svi)
        # Utilise le VNI deja present dans le JSON si disponible (coherence inter-DC)
        # Sinon calcule depuis vni_base + vlan_id
        if "vni" not in s or not s["vni"]:
            s["vni"] = vni_base + s["vlan_id"]
        enriched.append(s)
    return enriched

# ─── CALCULS AUTOMATIQUES ─────────────────────────────────────────────────────

def compute_all(data: dict) -> dict:
    fab  = data["fabric"]
    inv  = data["inventory"]

    spines_inv = inv["spines"]
    leafs_inv  = inv["leafs"]
    hosts_inv  = inv["hosts"]
    links      = data["interconnect_links"]

    vni_base = fab["vni_base"]

    def leaf_pair(leaf_idx: int) -> int:
        return (leaf_idx - 1) // 2

    def leaf_asn(leaf_idx: int) -> int:
        return fab["asn_leafs_base"] + leaf_pair(leaf_idx)

    def leaf_mlag_ips(leaf_idx: int):
        subnet = nth_subnet(fab["mlag_leaf_base"], 31, leaf_pair(leaf_idx))
        pos    = (leaf_idx - 1) % 2
        return str(subnet[pos]), str(subnet[1 - pos])

    def leaf_rd(leaf_idx: int) -> int:
        return fab["route_distinguisher_base"] + leaf_pair(leaf_idx) * 1000

    def leaf_mac(leaf_idx: int) -> str:
        pair = leaf_pair(leaf_idx)
        return f"{fab['virtual_router_mac_base']}{(pair + 1) * 12:02x}"

    def leaf_loopback1(leaf_idx: int) -> str:
        return nth_ip(fab["loopback1_base"], leaf_pair(leaf_idx) + 1)

    # ── Interconnects ─────────────────────────────────────────────────────────

    prefix   = fab["interconnect_prefix"]
    base_int = int(ipaddress.ip_network(fab["interconnect_base"], strict=False).network_address)
    step     = 2 ** (32 - prefix)

    spine_id_to_name = {host_id(n): n for n in spines_inv}
    leaf_id_to_name  = {host_id(n): n for n in leafs_inv}

    interconnects = []
    leafs_asn     = {}

    print("\n  Calcul des sous-reseaux d'interconnexion :")
    for i, link in enumerate(links):
        subnet     = ipaddress.ip_network(f"{ipaddress.ip_address(base_int + i * step)}/{prefix}")
        spine_ip   = str(subnet[0])
        leaf_ip    = str(subnet[1])
        network    = str(subnet)
        spine_name = spine_id_to_name.get(link["spine"], f"spine{link['spine']}")
        leaf_name  = leaf_id_to_name.get(link["leaf"],   f"leaf{link['leaf']}")

        interconnects.append({
            "spine":      link["spine"],
            "leaf":       link["leaf"],
            "spine_name": spine_name,
            "leaf_name":  leaf_name,
            "network":    network,
            "spine_ip":   spine_ip,
            "leaf_ip":    leaf_ip,
            "spine_eth":  link["spine_eth"],
            "leaf_eth":   link["leaf_eth"],
        })

        leafs_asn[link["leaf"]] = leaf_asn(link["leaf"])
        print(f"    {spine_name} <-> {leaf_name} : {network}  spine={spine_ip}  leaf={leaf_ip}")

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
        loopback0          = nth_ip(fab["loopback0_base"], 10 + l_id)

        raw_svis     = inv_data.get("svis", [])
        svis         = enrich_svis(raw_svis, vni_base)
        bgp_networks = compute_bgp_networks(svis, loopback0)

        is_border = inv_data.get("is_border_leaf", False)
        # ── Nettoyage dci_links : supprime les valeurs vides (ex: isn_loopback0: '')
        dci_links = clean_dci_links(inv_data.get("dci_links", []))

        computed_leafs[name] = {
            "loopback0_ip":        loopback0,
            "bgp_asn":             leaf_asn(l_id),
            "mlag_ip":             mlag_ip,
            "mlag_peer_ip":        mlag_peer,
            "virtual_router_mac":  leaf_mac(l_id),
            "loopback1_ip":        loopback1,
            "loopback_test_id":    fab["loopback_test_id"],
            "loopback_test_ip":    nth_ip(fab["loopback_test_base"], l_id * 10),
            "route_distinguisher": leaf_rd(l_id),
            "bgp_networks":        bgp_networks,
            "svis":                svis,
            "is_border_leaf":      is_border,
            "dci_links":           dci_links,
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

def generate_group_vars(data: dict, computed: dict, output_base: str):
    group_vars = os.path.join(output_base, "group_vars", "all")
    print("\ngroup_vars/all/")

    write_yaml(
        os.path.join(group_vars, "mlag.yml"),
        data["mlag"],
        "MLAG - Variables globales (ports peer-link detectes via LLDP)"
    )

    bgp = {
        "bgp_max_paths":  data["fabric"].get("bgp_max_paths", 4),
        "bgp_max_routes": data["fabric"].get("bgp_max_routes", 12000),
        "bgp_asn_spines": data["fabric"]["asn_spines"],
        "leafs_asn":      computed["leafs_asn"],
        "interconnects":  computed["interconnects"],
    }
    write_yaml(
        os.path.join(group_vars, "bgp.yml"),
        bgp,
        "BGP - Variables globales + interconnects (generes automatiquement)"
    )

    write_yaml(
        os.path.join(group_vars, "vxlan_evpn.yml"),
        data["vxlan_evpn"],
        "VXLAN EVPN - Variables globales communes a tous les leafs"
    )

def generate_host_vars(computed: dict, output_base: str):
    host_vars = os.path.join(output_base, "host_vars")
    print("\nhost_vars/")
    all_hosts = {**computed["spines"], **computed["leafs"], **computed["hosts"]}
    for hostname, vars_ in sorted(all_hosts.items()):
        write_yaml(
            os.path.join(host_vars, f"{hostname}.yml"),
            vars_,
            f"Variables specifiques a {hostname} (generees automatiquement)"
        )

def generate_hosts_file(data: dict, output_base: str):
    hosts_file = os.path.join(output_base, "hosts")
    dc_name    = data.get("dc_name", "arista")
    print("\nhosts")
    inv    = data["inventory"]
    av     = inv["arista_vars"]
    groups = {
        "spines": inv["spines"],
        "leafs":  inv["leafs"],
        "hosts":  inv["hosts"],
    }

    os.makedirs(output_base, exist_ok=True)
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

    with open(hosts_file, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  OK  {hosts_file}")

# ─── POINT D'ENTREE ───────────────────────────────────────────────────────────

def run_generation(vars_auto: dict = None, dc_name: str = None):
    """
    dc_name : nom du DC (ex: dc1, dc2) -> genere dans inventories/<dc_name>/
              Si None, utilise vars_auto["dc_name"] ou "production"
    """
    if vars_auto is None:
        vars_file = f"vars_auto_{dc_name}.json" if dc_name else VARS_FILE
        if not os.path.exists(vars_file):
            vars_file = VARS_FILE
        if not os.path.exists(vars_file):
            print(f"Fichier introuvable : {vars_file}")
            raise SystemExit(1)
        vars_auto = load_vars(vars_file)

    if dc_name is None:
        dc_name = vars_auto.get("dc_name", "production")

    output_base = os.path.join("inventories", dc_name)

    print(f"\nGeneration des fichiers Ansible pour : {dc_name}")
    print(f"Repertoire de sortie : {output_base}/")

    computed = compute_all(vars_auto)
    generate_group_vars(vars_auto, computed, output_base)
    generate_host_vars(computed, output_base)
    generate_hosts_file(vars_auto, output_base)
    print(f"\nTous les fichiers generes dans : {output_base}/")

if __name__ == "__main__":
    import sys
    dc = sys.argv[1] if len(sys.argv) > 1 else None
    run_generation(dc_name=dc)